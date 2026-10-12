"""Schedule a task DAG. Order, injection, and conditions are code, not a model."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Protocol

from fs_agent_runtime.agents import AgentRunner
from fs_agent_runtime.mail_draft import prepare_mail_draft
from fs_agent_runtime.planner import RulePlanner, is_place_to_place
from fs_agent_runtime.registry import AgentSpec, Catalog, load_catalog
from fs_agent_runtime.skills.loader import run_skill
from fs_agent_runtime.trip_memory import (
    BUSY_REPLY,
    canned_reply,
    is_busy_failure,
    is_trip_followup,
    resolve_utterance,
    slot_patch_from_delivered,
    slot_patch_from_text,
)
from fs_common import new_id
from fs_common.contracts.dag import ConditionOp, TaskDag, TaskNode
from fs_common.contracts.state import PendingApproval
from fs_common.otel import span
from fs_memory_service import DialogueBoard
from fs_memory_service.dialogue import merge_slots
from fs_skill_runtime import MemoryIdempotencyStore, SkillRuntime
from fs_skill_sdk import DeliverySemantics, SkillRisk, SkillSpec
from fs_tool_interceptor import DEFAULT_RECIPIENTS, ToolInterceptor, outbound_block

from fs_orchestrator.chat_planner import bind_history, reset_history

_PLACEHOLDER = re.compile(
    r"\$\{\{\s*([A-Za-z0-9_\u4e00-\u9fff]+)\.([A-Za-z0-9_\u4e00-\u9fff]+)\s*\}\}"
)
_PLAN_LIMIT = 3
_REPLAN_LIMIT = 2
_SKILL_COST = 100
_DEFAULT_BUDGET = 100_000

SEND_EMAIL = SkillSpec(
    name="send_email",
    description="Send one approved draft",
    risk=SkillRisk.HIGH,
    requires_approval=True,
    idempotency_required=True,
    safe_retry=False,
    delivery_semantics=DeliverySemantics.EXACTLY_ONCE_EFFECT,
)


class Planner(Protocol):
    def plan(self, text: str, *, attempt: int, errors: list[str]) -> TaskDag: ...


class Agents(Protocol):
    def execute(
        self, *, agent: AgentSpec, args: dict[str, Any], skill: str | None
    ) -> dict[str, Any]: ...


class KnowledgeQuery(Protocol):
    def query(
        self, *, query: str, tenant_id: str, top_k: int, scope_filter: str
    ) -> dict[str, Any]: ...


@dataclass
class RunOutcome:
    run_id: str
    status: str
    reply: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    task_results: dict[str, Any] = field(default_factory=dict)
    delivered: dict[int, dict[str, Any]] = field(default_factory=dict)
    replan_count: int = 0
    plan_attempts: int = 0
    pending_approval: PendingApproval | None = None
    action_key: str | None = None
    tenant_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.run_id,
            "run_id": self.run_id,
            "status": self.status,
            "reply": self.reply,
            "blocks": self.blocks,
            "replan_count": self.replan_count,
        }


class CollaborationService:
    def __init__(
        self,
        *,
        catalog: Catalog | None = None,
        planner: Planner | None = None,
        agents: Agents | None = None,
        sender: Any = None,
        knowledge: KnowledgeQuery | None = None,
        dialogue: DialogueBoard | None = None,
        budget_tokens: int = _DEFAULT_BUDGET,
        traces: list[dict[str, str]] | None = None,
        recipients: Collection[str] | None = None,
    ) -> None:
        self.catalog = catalog or load_catalog()
        self.planner = planner or RulePlanner()
        self.agents = agents or AgentRunner()
        self.knowledge = knowledge
        self.dialogue = dialogue or DialogueBoard.in_memory()
        self._budget_tokens = budget_tokens
        self._traces = traces if traces is not None else []
        self._recipients = (
            frozenset(recipients) if recipients is not None else DEFAULT_RECIPIENTS
        )
        self._outcomes: dict[str, RunOutcome] = {}
        self._skills = SkillRuntime(
            store=MemoryIdempotencyStore(),
            interceptor=ToolInterceptor(default_deny=True),
        )
        self._skills.register(SEND_EMAIL)
        self._skills.register_handler("send_email", sender or _default_send)
        for decl in self.catalog.skills:
            self._skills.register(
                SkillSpec(
                    name=decl.name,
                    description=decl.name,
                    input_schema=dict(decl.input),
                    timeout_sec=_timeout_seconds(decl.timeout),
                    requires_approval=False,
                    idempotency_required=False,
                    safe_retry=True,
                    risk=SkillRisk.LOW,
                )
            )

    def execute(
        self, *, task_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        outcome = self.execute_outcome(task_id=task_id, payload=payload)
        return outcome.as_dict()

    def execute_outcome(
        self, *, task_id: str | None, payload: dict[str, Any]
    ) -> RunOutcome:
        text = str(payload.get("user_input") or payload.get("text") or "")
        spoken = str(payload.get("user_text") or text)
        run_id = task_id or new_id("run")
        tenant_id = str(payload.get("tenant_id") or "")
        user_id = str(payload.get("user_id") or "")
        conversation_id = str(payload.get("conversation_id") or "default")
        state = self.dialogue.load(tenant_id, user_id, conversation_id=conversation_id)
        history = bind_history(state.turns)
        try:
            return self._execute_with_memory(
                text=text,
                spoken=spoken,
                run_id=run_id,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                task_id=task_id,
                payload=payload,
                slots=state.slots,
                turns=state.turns,
            )
        finally:
            reset_history(history)

    def _execute_with_memory(
        self,
        *,
        text: str,
        spoken: str,
        run_id: str,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        task_id: str | None,
        payload: dict[str, Any],
        slots: dict[str, Any],
        turns: list[dict[str, str]],
    ) -> RunOutcome:
        del task_id
        continued = resolve_utterance(text, slots, turns)
        asked = canned_reply(continued)
        if asked:
            return self._finish(
                run_id,
                tenant_id,
                user_id,
                conversation_id,
                spoken,
                RunOutcome(
                    run_id=run_id, status="done", reply=asked, tenant_id=tenant_id
                ),
            )
        if continued:
            text = continued
        errors: list[str] = []
        dag: TaskDag | None = None
        attempts = 0
        self._note("run", run_id)
        for attempt in range(1, _PLAN_LIMIT + 1):
            attempts = attempt
            try:
                self._note("node", "planner")
                self._note("llm", getattr(self.planner, "model_name", "planner"))
                dag = self.planner.plan(text, attempt=attempt, errors=errors)
            except (ValueError, RuntimeError, TypeError) as exc:
                errors = [str(exc)]
                dag = None
                continue
            _canonicalize(dag)
            errors = validate_dag(dag, self.catalog)
            errors.extend(missing_coverage(text, dag))
            if not errors:
                break
        if dag is not None and not errors:
            scope = str(payload.get("knowledge_scope") or "")
            if scope:
                for task in dag.tasks:
                    if task.args.get("skill") == "rag_query":
                        task.args["scope_filter"] = scope
        if dag is None or errors:
            outcome = RunOutcome(
                run_id=run_id,
                status="degraded",
                reply="这次安排没有通过校验，请换一种说法再试一次。",
                plan_attempts=attempts,
                tenant_id=tenant_id,
            )
            return self._finish(
                run_id, tenant_id, user_id, conversation_id, spoken, outcome
            )
        outcome = self._run_dag(
            dag,
            text=text,
            run_id=run_id,
            tenant_id=tenant_id,
            plan_attempts=attempts,
        )
        return self._finish(
            run_id, tenant_id, user_id, conversation_id, spoken, outcome
        )

    def _finish(
        self,
        run_id: str,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        spoken: str,
        outcome: RunOutcome,
    ) -> RunOutcome:
        self._outcomes[run_id] = outcome
        try:
            self.dialogue.commit(
                tenant_id,
                user_id,
                user_text=spoken,
                assistant_text=outcome.reply,
                slot_patch=merge_slots(
                    slot_patch_from_text(spoken),
                    slot_patch_from_delivered(outcome.delivered),
                ),
                conversation_id=conversation_id,
            )
        except Exception:
            # The user still gets this turn's answer if the board cannot be saved.
            return outcome
        return outcome

    def _retrieve(self, args: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        if self.knowledge is None:
            raise RuntimeError("rag_query is not connected")
        raw_top_k = args.get("top_k", 5)
        top_k = int(raw_top_k) if isinstance(raw_top_k, int | float) else 5
        found = self.knowledge.query(
            query=str(args.get("query") or ""),
            tenant_id=tenant_id,
            top_k=top_k,
            scope_filter=str(args.get("scope_filter") or ""),
        )
        context = found.get("retrieved_context")
        if not isinstance(context, dict) or context.get("role") != "data":
            found = {
                **found,
                "retrieved_context": {
                    "role": "data",
                    "query": str(args.get("query") or ""),
                    "citations": [],
                },
            }
        return found

    def approve(self, run_id: str, *, tenant_id: str) -> dict[str, str]:
        outcome = self._outcomes.get(run_id)
        if outcome is None or outcome.tenant_id != tenant_id:
            raise KeyError(run_id)
        if outcome.pending_approval is None or not outcome.action_key:
            raise ValueError("this run is not waiting for approval")
        draft = outcome.pending_approval.payload
        to = str(draft.get("收件人") or draft.get("to") or "")
        subject = str(draft.get("主题") or draft.get("subject") or "")
        body = str(draft.get("正文") or draft.get("body") or "")
        if not body.strip():
            outcome.status = "done"
            outcome.pending_approval = None
            outcome.reply = "还没有邮件内容，请说一下要发送的内容。"
            return {
                "run_id": run_id,
                "status": outcome.status,
                "reply": outcome.reply,
                "disposition": "denied",
                "result_ref": "",
            }
        blocked = outbound_block(to, body, recipients=self._recipients)
        if blocked:
            outcome.status = "done"
            outcome.pending_approval = None
            outcome.reply = blocked
            return {
                "run_id": run_id,
                "status": outcome.status,
                "reply": outcome.reply,
                "disposition": "denied",
                "result_ref": "",
            }
        payload = {
            "收件人": to,
            "主题": subject,
            "正文": body,
            "to": to,
            "subject": subject,
            "body": body,
            "idempotency_key": outcome.action_key,
        }
        sent = self._skills.apply(
            "send_email",
            payload,
            idempotency_key=outcome.action_key,
            tenant_id=tenant_id,
            run_id=run_id,
            approved=True,
        )
        if sent.disposition in {"sent", "replayed"}:
            outcome.status = "done"
            outcome.pending_approval = None
            outcome.reply = sent.result_ref or "邮件已发送"
        else:
            outcome.status = "done"
            outcome.pending_approval = None
            outcome.reply = sent.error_message or "邮件未发送。"
        return {
            "run_id": run_id,
            "status": outcome.status,
            "reply": outcome.reply,
            "disposition": sent.disposition,
            "result_ref": sent.result_ref or "",
        }

    def cancel(self, run_id: str, *, tenant_id: str) -> dict[str, str]:
        outcome = self._outcomes.get(run_id)
        if outcome is None or outcome.tenant_id != tenant_id:
            raise KeyError(run_id)
        if outcome.pending_approval is None:
            raise ValueError("this run is not waiting for approval")
        outcome.status = "cancelled"
        outcome.pending_approval = None
        outcome.action_key = None
        outcome.reply = "已取消发送邮件。"
        return {
            "run_id": run_id,
            "status": outcome.status,
            "reply": outcome.reply,
            "disposition": "cancelled",
            "result_ref": "",
        }

    def _dispatch(
        self,
        agent: AgentSpec,
        args: dict[str, Any],
        skill_name: str | None,
        tenant_id: str,
    ) -> dict[str, Any]:
        if skill_name is None or not self._skills.registered(skill_name):
            self._note("agent", agent.name)
            return self.agents.execute(agent=agent, args=args, skill=skill_name)

        def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            if skill_name == "rag_query":
                return run_skill(
                    "rag_query",
                    arguments,
                    query=lambda payload: self._retrieve(payload, tenant_id),
                )
            if skill_name in {"subtract", "amount_from_text"}:
                return run_skill(skill_name, arguments)
            return self.agents.execute(agent=agent, args=arguments, skill=skill_name)

        self._note("agent", agent.name)
        self._note("skill", skill_name)

        output = self._skills.call(
            skill_name,
            args,
            handler,
            allowed=set(agent.allowed_skills),
        )
        if not isinstance(output, dict):
            raise TypeError(f"skill {skill_name} did not return an object")
        return output

    def _note(self, kind: str, name: str) -> None:
        self._traces.append({"kind": kind, "name": name})
        with span(name, kind=kind):
            return

    def _run_dag(
        self,
        dag: TaskDag,
        *,
        text: str,
        run_id: str,
        tenant_id: str,
        plan_attempts: int,
    ) -> RunOutcome:
        completed: set[int] = set()
        skipped: set[int] = set()
        results: dict[str, Any] = {}
        delivered: dict[int, dict[str, Any]] = {}
        blocks: list[dict[str, Any]] = []
        replies: list[str] = []
        pending: PendingApproval | None = None
        action_key: str | None = None
        replan_count = 0
        current = dag
        failures: list[str] = []
        spent = 0
        while True:
            progress = True
            while progress and not failures:
                ready = _ready(current, completed, skipped)
                progress = bool(ready)
                prepared = [(task, _inject(task, results)) for task in ready]
                for task, args in prepared:
                    delivered[task.id] = args
                    agent = self.catalog.agent(task.agent)
                    if agent is None:
                        skipped.add(task.id)
                        replies.append(f"没有名为{task.agent}的助手")
                        continue
                    passed, reason = _condition(task, results)
                    if not passed:
                        skipped.add(task.id)
                        replies.append(reason)
                        continue
                    skill = task.args.get("skill")
                    skill_name = skill if isinstance(skill, str) else None
                    if spent + _SKILL_COST > self._budget_tokens:
                        return RunOutcome(
                            run_id=run_id,
                            status="degraded",
                            reply="这次会话已超过预算，请缩小问题后再试一次。",
                            blocks=blocks,
                            task_results=results,
                            delivered=delivered,
                            replan_count=replan_count,
                            plan_attempts=plan_attempts,
                            tenant_id=tenant_id,
                        )
                    spent += _SKILL_COST
                    try:
                        output = self._dispatch(agent, args, skill_name, tenant_id)
                    except ValueError as exc:
                        if is_busy_failure(str(exc)):
                            return RunOutcome(
                                run_id=run_id,
                                status="done",
                                reply=BUSY_REPLY,
                                blocks=blocks,
                                task_results=results,
                                delivered=delivered,
                                replan_count=replan_count,
                                plan_attempts=plan_attempts,
                                tenant_id=tenant_id,
                            )
                        horizon = str(exc).removeprefix("[HORIZON] ")
                        if horizon != str(exc):
                            return RunOutcome(
                                run_id=run_id,
                                status="done",
                                reply=horizon,
                                blocks=blocks,
                                task_results=results,
                                delivered=delivered,
                                replan_count=replan_count,
                                plan_attempts=plan_attempts,
                                tenant_id=tenant_id,
                            )
                        failures.append(str(exc))
                        break
                    if task.output_key:
                        results[task.output_key] = output
                    completed.add(task.id)
                    if agent.side_effect == "irreversible" or agent.name == "发邮件":
                        draft = _mail_payload(output, args)
                        body = str(draft.get("正文") or draft.get("body") or "").strip()
                        if output.get("needs_body") or not body:
                            reminder = str(
                                output.get("summary")
                                or "还没有邮件内容，请说一下要发送的内容。"
                            )
                            replies.append(reminder)
                            blocks.append({"kind": "mail", "body": output})
                        else:
                            pending = PendingApproval(
                                task_id=task.id,
                                action_type="send_email",
                                payload=dict(draft),
                            )
                            action_key = f"act_{run_id}_send"
                            preview = str(output.get("summary") or "").strip()
                            if preview:
                                replies.append(preview)
                            blocks.append({"kind": "draft", "body": draft})
                    elif task.output_key:
                        blocks.append({"kind": task.output_key, "body": output})
            if failures:
                if replan_count >= _REPLAN_LIMIT:
                    return RunOutcome(
                        run_id=run_id,
                        status="degraded",
                        reply="这次安排没有做完，请换一种说法再试一次。",
                        blocks=blocks,
                        task_results=results,
                        delivered=delivered,
                        replan_count=replan_count,
                        plan_attempts=plan_attempts,
                        tenant_id=tenant_id,
                    )
                replan_count += 1
                try:
                    current = self.planner.plan(
                        text, attempt=plan_attempts + replan_count, errors=failures
                    )
                except (ValueError, RuntimeError, TypeError):
                    return RunOutcome(
                        run_id=run_id,
                        status="degraded",
                        reply="这次安排没有通过校验，请换一种说法再试一次。",
                        replan_count=replan_count,
                        plan_attempts=plan_attempts,
                        tenant_id=tenant_id,
                    )
                _canonicalize(current)
                fresh_errors = validate_dag(current, self.catalog)
                fresh_errors.extend(missing_coverage(text, current))
                if fresh_errors:
                    return RunOutcome(
                        run_id=run_id,
                        status="degraded",
                        reply="这次安排没有通过校验，请换一种说法再试一次。",
                        replan_count=replan_count,
                        plan_attempts=plan_attempts,
                        tenant_id=tenant_id,
                    )
                completed = set()
                skipped = set()
                results = {}
                delivered = {}
                blocks = []
                replies = []
                pending = None
                action_key = None
                failures = []
                continue
            missing = _join_gaps(current, results, self.catalog, completed)
            if not missing:
                break
            if replan_count >= _REPLAN_LIMIT:
                return RunOutcome(
                    run_id=run_id,
                    status="degraded",
                    reply="这次安排没有做完，请换一种说法再试一次。",
                    blocks=blocks,
                    task_results=results,
                    delivered=delivered,
                    replan_count=replan_count,
                    plan_attempts=plan_attempts,
                    tenant_id=tenant_id,
                )
            replan_count += 1
            try:
                current = self.planner.plan(
                    text, attempt=plan_attempts + replan_count, errors=missing
                )
            except (ValueError, RuntimeError, TypeError):
                return RunOutcome(
                    run_id=run_id,
                    status="degraded",
                    reply="这次安排没有通过校验，请换一种说法再试一次。",
                    replan_count=replan_count,
                    plan_attempts=plan_attempts,
                    tenant_id=tenant_id,
                )
            _canonicalize(current)
            fresh_errors = validate_dag(current, self.catalog)
            fresh_errors.extend(missing_coverage(text, current))
            if fresh_errors:
                return RunOutcome(
                    run_id=run_id,
                    status="degraded",
                    reply="这次安排没有通过校验，请换一种说法再试一次。",
                    replan_count=replan_count,
                    plan_attempts=plan_attempts,
                    tenant_id=tenant_id,
                )
            completed = set()
            skipped = set()
            results = {}
            delivered = {}
            blocks = []
            replies = []
            pending = None
            action_key = None
        status = "awaiting_approval" if pending is not None else "done"
        gap_reply = _gap_reply(results)
        if gap_reply:
            replies.append(gap_reply)
        else:
            weather_reply = _weather_reply(results)
            if weather_reply:
                replies.append(weather_reply)
            if pending is None:
                mail_reply = _mail_reply(results)
                if mail_reply and mail_reply not in replies:
                    replies.append(mail_reply)
            itinerary_reply = _itinerary_reply(results)
            if itinerary_reply:
                replies.append(itinerary_reply)
        return RunOutcome(
            run_id=run_id,
            status=status,
            reply="".join(replies),
            blocks=blocks,
            task_results=results,
            delivered=delivered,
            replan_count=replan_count,
            plan_attempts=plan_attempts,
            pending_approval=pending,
            action_key=action_key,
            tenant_id=tenant_id,
        )


_ARG_ALIASES = {
    "出发地": "起点",
    "起点站": "起点",
    "目的地": "终点",
    "终点站": "终点",
    "交通方式": "方式",
    "出行方式": "方式",
}


def _canonicalize(dag: TaskDag) -> None:
    for task in dag.tasks:
        merged = dict(task.args)
        for alias, canonical in _ARG_ALIASES.items():
            if alias not in merged:
                continue
            if canonical not in merged:
                merged[canonical] = merged[alias]
            del merged[alias]
        task.args = merged


def missing_coverage(text: str, dag: TaskDag) -> list[str]:
    skills = [task.args.get("skill") for task in dag.tasks]
    missing: list[str] = []
    if "天气" in text and "查天气" not in skills:
        missing.append("用户要求查天气，任务单里没有查天气")
    if is_place_to_place(text) or is_trip_followup(text):
        if "规划行程" not in skills:
            missing.append("用户在问从一地到另一地，任务单里没有规划行程")
    elif (
        any(word in text for word in ("路线", "打车", "乘车"))
        and "查路线" not in skills
    ):
        missing.append("用户要求查路线，任务单里没有查路线")
    if any(word in text for word in ("发邮件", "发信", "发给主人", "给你的主人")):
        if "发邮件" not in skills and "发邮件" not in [
            task.agent for task in dag.tasks
        ]:
            missing.append("用户要求发邮件，任务单里没有发邮件")
    policy = any(
        word in text
        for word in ("差旅", "报销", "制度", "知识库", "员工手册", "公司规定")
    )
    compare = any(word in text for word in ("超过", "超了", "对照"))
    if policy and "rag_query" not in skills:
        missing.append("用户要求查阅公司标准，任务单里没有 rag_query")
    if policy and compare and "amount_from_text" not in skills:
        missing.append("用户要求和标准比较，任务单里没有 amount_from_text")
    if policy and compare and "subtract" not in skills:
        missing.append("用户要求算出差额，任务单里没有 subtract")
    return missing


def _timeout_seconds(raw: str) -> int:
    digits = "".join(char for char in raw if char.isdigit())
    return int(digits) if digits else 30


def validate_dag(dag: TaskDag, catalog: Catalog) -> list[str]:
    errors: list[str] = []
    by_id = {task.id: task for task in dag.tasks}
    if _has_cycle(dag.tasks):
        errors.append("task dependencies form a cycle")
    produced = {task.output_key: task for task in dag.tasks if task.output_key}
    for task in dag.tasks:
        agent = catalog.agent(task.agent)
        if agent is None:
            errors.append(f"task {task.id} names unknown agent {task.agent}")
            continue
        skill = task.args.get("skill")
        if isinstance(skill, str) and skill not in agent.allowed_skills:
            errors.append(
                f"task {task.id} uses skill {skill} outside {agent.name} allowed_skills"
            )
        for match in _PLACEHOLDER.finditer(str(task.args)):
            key, child = match.group(1), match.group(2)
            source = produced.get(key)
            if source is None:
                errors.append(f"task {task.id} references unknown output {key}")
                continue
            source_agent = catalog.agent(source.agent)
            field_name = f"{key}.{child}"
            if source_agent is None or field_name not in source_agent.field_types():
                errors.append(
                    f"task {task.id} references undeclared field {field_name}"
                )
        if task.condition is None:
            continue
        if task.condition.op not in set(ConditionOp):
            errors.append(f"task {task.id} uses an unknown operator")
        owner = produced.get(task.condition.field.split(".", 1)[0])
        field_type = ""
        if owner is None:
            errors.append(
                f"task {task.id} condition uses unknown output {task.condition.field}"
            )
        else:
            owner_agent = catalog.agent(owner.agent)
            field_type = (
                owner_agent.field_types().get(task.condition.field, "")
                if owner_agent
                else ""
            )
            if not field_type:
                errors.append(
                    f"task {task.id} condition uses undeclared field {task.condition.field}"
                )
        if task.condition.op in {ConditionOp.LT, ConditionOp.GT} and field_type not in {
            "",
            "number",
        }:
            errors.append(f"task {task.id} compares a non-number field")
        for dep in task.deps:
            if dep not in by_id:
                errors.append(f"task {task.id} depends on unknown task {dep}")
    return errors


def _ready(dag: TaskDag, completed: set[int], skipped: set[int]) -> list[TaskNode]:
    done = completed | skipped
    return [
        task
        for task in dag.tasks
        if task.id not in done and all(dep in done for dep in task.deps)
    ]


def _inject(task: TaskNode, results: dict[str, Any]) -> dict[str, Any]:
    filled = _fill(task.args, results)
    if not isinstance(filled, dict):
        return {}
    filled.pop("skill", None)
    return filled


def _fill(value: Any, results: dict[str, Any]) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            data = results.get(match.group(1))
            if isinstance(data, dict) and match.group(2) in data:
                return str(data[match.group(2)])
            return match.group(0)

        return _PLACEHOLDER.sub(replace, value)
    if isinstance(value, dict):
        return {key: _fill(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, results) for item in value]
    return value


def _condition(task: TaskNode, results: dict[str, Any]) -> tuple[bool, str]:
    if task.condition is None:
        return True, ""
    try:
        actual = _lookup(task.condition.field, results)
    except KeyError:
        return False, f"缺少{task.condition.field}，后续步骤未执行"
    passed = _compare(actual, task.condition.op, task.condition.value)
    if passed:
        return True, ""
    if (
        task.condition.field == "hotel.avg_price"
        and task.condition.op is ConditionOp.LT
    ):
        return (
            False,
            f"查到均价 {_shown(actual)} 元，未低于 {_shown(task.condition.value)}，邮件未发送",
        )
    return False, "条件未满足，后续步骤未执行"


def _lookup(field: str, results: dict[str, Any]) -> Any:
    key, separator, child = field.partition(".")
    data = results[key]
    if not separator:
        return data
    if not isinstance(data, dict) or child not in data:
        raise KeyError(field)
    return data[child]


def _compare(left: Any, op: ConditionOp, right: Any) -> bool:
    if op is ConditionOp.LT:
        return float(left) < float(right)
    if op is ConditionOp.GT:
        return float(left) > float(right)
    if op is ConditionOp.EQ:
        return left == right
    if op is ConditionOp.IN:
        return left in right
    return False


def _join_gaps(
    dag: TaskDag, results: dict[str, Any], catalog: Catalog, completed: set[int]
) -> list[str]:
    missing: list[str] = []
    for task in dag.tasks:
        if task.id not in completed or not task.output_key:
            continue
        agent = catalog.agent(task.agent)
        if agent is None:
            continue
        value = results.get(task.output_key)
        prefix = f"{task.output_key}."
        for path in agent.field_types():
            if path == task.output_key:
                if value is None:
                    missing.append(path)
                continue
            if not path.startswith(prefix):
                continue
            child = path[len(prefix) :]
            if "." in child:
                continue
            if not isinstance(value, dict) or child not in value:
                missing.append(path)
    return missing


def _has_cycle(tasks: list[TaskNode]) -> bool:
    graph = {task.id: list(task.deps) for task in tasks}
    visiting: set[int] = set()
    visited: set[int] = set()

    def walk(node: int) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, []):
            if walk(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(walk(node) for node in graph)


def _itinerary_reply(results: dict[str, Any]) -> str:
    itinerary = results.get("itinerary")
    if not isinstance(itinerary, dict):
        return ""
    summary = itinerary.get("summary")
    if not isinstance(summary, str):
        return ""
    return summary.strip()


def _mail_reply(results: dict[str, Any]) -> str:
    mail = results.get("mail")
    if not isinstance(mail, dict):
        return ""
    summary = mail.get("summary")
    if not isinstance(summary, str):
        return ""
    return summary.strip()


def _weather_reply(results: dict[str, Any]) -> str:
    weather = results.get("weather")
    if not isinstance(weather, dict):
        return ""
    summary = weather.get("summary")
    if not isinstance(summary, str):
        return ""
    return summary.strip()


def _gap_reply(results: dict[str, Any]) -> str:
    gap = results.get("gap")
    if not isinstance(gap, dict) or "value" not in gap:
        return ""
    number = float(gap["value"])
    if number > 0:
        return f"超了 {_shown(number)} 元"
    if number < 0:
        return f"比标准低 {_shown(abs(number))} 元"
    return "没有超过标准"


def _shown(value: Any) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


def _mail_payload(output: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    draft = output.get("draft")
    if isinstance(draft, dict):
        return dict(draft)
    prepared = prepare_mail_draft(args)
    prepared_draft = prepared.get("draft")
    if isinstance(prepared_draft, dict):
        return dict(prepared_draft)
    return {
        "to": str(args.get("收件人") or args.get("to") or ""),
        "subject": str(args.get("主题") or args.get("subject") or ""),
        "body": str(args.get("正文") or args.get("正文模板") or args.get("body") or ""),
        "收件人": str(args.get("收件人") or args.get("to") or ""),
        "主题": str(args.get("主题") or args.get("subject") or ""),
        "正文": str(args.get("正文") or args.get("正文模板") or args.get("body") or ""),
    }


def _default_send(arguments: dict[str, Any]) -> str:
    result = run_skill(
        "发邮件",
        {
            "收件人": str(arguments.get("收件人") or arguments.get("to") or ""),
            "主题": str(arguments.get("主题") or arguments.get("subject") or ""),
            "正文": str(arguments.get("正文") or arguments.get("body") or ""),
        },
    )
    if not isinstance(result, dict):
        raise TypeError("邮件发送没有返回结果")
    summary = str(result.get("summary") or "")
    if not result.get("sent"):
        raise RuntimeError(summary or "邮件未发送")
    return summary or f"email:{arguments.get('idempotency_key')}"
