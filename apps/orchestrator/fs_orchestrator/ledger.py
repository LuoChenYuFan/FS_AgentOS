"""Persist a LangGraph thread in the layer-0 checkpoints table.

One database transaction covers one node. A crash keeps the last committed node.
Side effects commit their action key before the call, and the receipt before this
checkpoint, so a crash in that window cannot send the same email twice.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from fs_agent_runtime import build_durable_graph
from fs_common import new_id
from fs_memory_service import MemoryService, PostgresMemoryStore
from fs_skill_runtime import EffectOutcome, SkillRuntime
from fs_skill_sdk import DeliverySemantics, SkillRisk, SkillSpec
from fs_tool_interceptor import outbound_block
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from fs_orchestrator.idempotency import PostgresIdempotencyStore
from fs_orchestrator.postgres import connect, migrate

SEND_EMAIL = SkillSpec(
    name="send_email",
    description="Send one approved draft",
    risk=SkillRisk.HIGH,
    requires_approval=True,
    idempotency_required=True,
    safe_retry=False,
    delivery_semantics=DeliverySemantics.EXACTLY_ONCE_EFFECT,
)


def _default_send_email(arguments: dict[str, Any]) -> str:
    return f"email:{arguments['idempotency_key']}"


class DurabilityError(Exception):
    def __init__(self, message: str, *, code: str = "durability_error") -> None:
        super().__init__(message)
        self.code = code


class LedgerSaver(BaseCheckpointSaver):
    """LangGraph checkpointer. thread_id is the run_id."""

    def __init__(self, conn, *, serde: SerializerProtocol | None = None) -> None:
        super().__init__(serde=serde)
        self._conn = conn

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        wanted = get_checkpoint_id(config)
        row = self._find(thread_id, checkpoint_ns, wanted)
        if row is None:
            return None
        return self._tuple_from_row(row)

    def list(
        self,
        config: dict | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        del filter, before
        if config is None:
            return
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        rows = self._conn.execute(
            """
            SELECT state_snapshot
            FROM checkpoints
            WHERE run_id = %s AND state_snapshot->>'checkpoint_ns' = %s
            ORDER BY step_seq DESC
            """,
            (thread_id, checkpoint_ns),
        ).fetchall()
        for row in rows:
            if limit is not None and limit <= 0:
                break
            if limit is not None:
                limit -= 1
            yield self._tuple_from_row(row)

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> dict:
        del new_versions
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        meta = get_checkpoint_metadata(config, metadata)
        values = checkpoint.get("channel_values") or {}
        completed = list(values.get("completed") or [])
        node_name = completed[-1] if completed else "start"
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(meta)
        snapshot = {
            "thread_id": thread_id,
            "message": values.get("message"),
            "completed": completed,
            "draft": values.get("draft"),
            "final": values.get("final"),
            "checkpoint_ns": checkpoint_ns,
            "langgraph_id": checkpoint["id"],
            "parent_id": config["configurable"].get("checkpoint_id"),
            "checkpoint_type": checkpoint_type,
            "checkpoint_b64": base64.b64encode(checkpoint_blob).decode("ascii"),
            "metadata_type": metadata_type,
            "metadata_b64": base64.b64encode(metadata_blob).decode("ascii"),
            "writes": [],
        }
        step = self._conn.execute(
            "SELECT COALESCE(MAX(step_seq), -1) + 1 AS step FROM checkpoints WHERE run_id = %s",
            (thread_id,),
        ).fetchone()["step"]
        self._conn.execute(
            """
            INSERT INTO checkpoints (id, run_id, node_name, step_seq, state_snapshot)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (new_id("ckpt"), thread_id, node_name, step, Json(snapshot)),
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"]["checkpoint_id"]
        row = self._conn.execute(
            """
            SELECT id, state_snapshot
            FROM checkpoints
            WHERE run_id = %s AND state_snapshot->>'langgraph_id' = %s
            FOR UPDATE
            """,
            (thread_id, checkpoint_id),
        ).fetchone()
        if row is None:
            return
        snapshot = dict(row["state_snapshot"])
        existing = {
            (item["task_id"], item["idx"])
            for item in snapshot.get("writes", [])
            if item["idx"] >= 0
        }
        stored = list(snapshot.get("writes", []))
        for idx, (channel, value) in enumerate(writes):
            mapped = WRITES_IDX_MAP.get(channel, idx)
            key = (task_id, mapped)
            if mapped >= 0 and key in existing:
                continue
            value_type, blob = self.serde.dumps_typed(value)
            stored.append(
                {
                    "task_id": task_id,
                    "idx": mapped,
                    "channel": channel,
                    "task_path": task_path,
                    "value_type": value_type,
                    "value_b64": base64.b64encode(blob).decode("ascii"),
                }
            )
        snapshot["writes"] = stored
        self._conn.execute(
            "UPDATE checkpoints SET state_snapshot = %s WHERE id = %s",
            (Json(snapshot), row["id"]),
        )

    def _find(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str | None) -> dict | None:
        if checkpoint_id:
            return self._conn.execute(
                """
                SELECT state_snapshot
                FROM checkpoints
                WHERE run_id = %s
                  AND state_snapshot->>'checkpoint_ns' = %s
                  AND state_snapshot->>'langgraph_id' = %s
                """,
                (thread_id, checkpoint_ns, checkpoint_id),
            ).fetchone()
        return self._conn.execute(
            """
            SELECT state_snapshot
            FROM checkpoints
            WHERE run_id = %s AND state_snapshot->>'checkpoint_ns' = %s
            ORDER BY step_seq DESC
            LIMIT 1
            """,
            (thread_id, checkpoint_ns),
        ).fetchone()

    def _tuple_from_row(self, row: dict) -> CheckpointTuple:
        snapshot = row["state_snapshot"]
        checkpoint = self.serde.loads_typed(
            (snapshot["checkpoint_type"], base64.b64decode(snapshot["checkpoint_b64"]))
        )
        metadata = self.serde.loads_typed(
            (snapshot["metadata_type"], base64.b64decode(snapshot["metadata_b64"]))
        )
        pending = []
        for item in snapshot.get("writes", []):
            pending.append(
                (
                    item["task_id"],
                    item["channel"],
                    self.serde.loads_typed((item["value_type"], base64.b64decode(item["value_b64"]))),
                )
            )
        parent_id = snapshot.get("parent_id")
        ns = snapshot.get("checkpoint_ns") or ""
        thread = snapshot["thread_id"]
        config = {
            "configurable": {
                "thread_id": thread,
                "checkpoint_ns": ns,
                "checkpoint_id": snapshot["langgraph_id"],
            }
        }
        parent = None
        if parent_id:
            parent = {
                "configurable": {
                    "thread_id": thread,
                    "checkpoint_ns": ns,
                    "checkpoint_id": parent_id,
                }
            }
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            pending_writes=pending,
            parent_config=parent,
        )


class DurableRuns:
    """Create a run, execute one node per commit, and resume by run_id.

    The run entry key only stops a second run from opening. The approval row
    carries a different key, and that key is what the send claims.
    """

    def __init__(
        self,
        *,
        sender: Callable[[dict[str, Any]], str] | None = None,
        after_effect: Callable[[EffectOutcome], None] | None = None,
        memory: MemoryService | None = None,
    ) -> None:
        self._sender = sender or _default_send_email
        self._after_effect = after_effect
        self._memory = memory or MemoryService(store=PostgresMemoryStore(connect))

    def run(
        self,
        *,
        run_id: str,
        tenant_id: str,
        user_id: str,
        user_input: str,
        idempotency_key: str,
        session_id: str | None = None,
        max_steps: int | None = None,
    ) -> dict[str, str]:
        conn = connect()
        try:
            migrate(conn)
            existing = self._by_key(conn, tenant_id, idempotency_key)
            if existing:
                if existing["user_input"] != user_input:
                    raise DurabilityError("idempotency key reused", code="conflict")
                return _view(existing)
            try:
                self._ensure_run(
                    conn,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    user_input=user_input,
                    idempotency_key=idempotency_key,
                    session_id=session_id or f"sess_{run_id}",
                )
                conn.commit()
            except UniqueViolation:
                conn.rollback()
                existing = self._by_key(conn, tenant_id, idempotency_key)
                if existing is None or existing["user_input"] != user_input:
                    raise DurabilityError("idempotency key reused", code="conflict") from None
                return _view(existing)
            return self._advance(conn, run_id, tenant_id, user_input, max_steps=max_steps, approving=False)
        finally:
            conn.close()

    def pause(self, run_id: str, *, tenant_id: str) -> dict[str, str]:
        return self._set_status(run_id, tenant_id, {"running", "awaiting_approval"}, "suspended")

    def cancel(self, run_id: str, *, tenant_id: str) -> dict[str, str]:
        return self._set_status(
            run_id,
            tenant_id,
            {"running", "suspended", "awaiting_approval"},
            "failed",
        )

    def resume(self, run_id: str, *, tenant_id: str) -> dict[str, str]:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                row = self._lock(conn, run_id, tenant_id)
                if row["status"] in {"done", "failed"}:
                    return _view(row)
                if _effect_unknown(row):
                    return _view(row)
                if row["status"] not in {"running", "suspended", "awaiting_approval"}:
                    raise DurabilityError("run cannot resume", code="conflict")
                approving = row["status"] == "awaiting_approval"
                if row["status"] != "running":
                    conn.execute(
                        """
                        UPDATE runs
                        SET status = 'running', resumed_at = now(), updated_at = now()
                        WHERE id = %s
                        """,
                        (run_id,),
                    )
            return self._advance(conn, run_id, tenant_id, None, max_steps=None, approving=approving)
        finally:
            conn.close()

    def _advance(
        self,
        conn,
        run_id: str,
        tenant_id: str,
        user_input: str | None,
        *,
        max_steps: int | None,
        approving: bool,
    ) -> dict[str, str]:
        taken = 0
        while True:
            with conn.transaction():
                row = self._lock(conn, run_id, tenant_id)
                if row["status"] == "suspended":
                    return _view(row)
                if row["status"] in {"done", "failed"}:
                    return _view(row)
                if row["status"] == "awaiting_approval" and not approving:
                    return _view(row)
                approving_now = approving
                approving = False
                if max_steps is not None and taken >= max_steps:
                    return _view(row)
                snapshot = self._latest_snapshot(conn, run_id)
                completed = list(snapshot.get("completed") or [])
                receipt: str | None = None
                if "draft" in completed and "join" not in completed:
                    proceed, receipt = self._send_approved(
                        conn,
                        run_id=run_id,
                        tenant_id=tenant_id,
                        draft=str(snapshot.get("draft") or ""),
                        approving=approving_now,
                    )
                    if not proceed:
                        return _view(self._lock(conn, run_id, tenant_id))
                saver = LedgerSaver(conn)
                graph = build_durable_graph(saver)
                config = {"configurable": {"thread_id": run_id}}
                existing = saver.get_tuple(config)
                facts = self._long_term(conn, run_id, tenant_id)
                if existing is None:
                    text = user_input or ""
                    graph.invoke(
                        {
                            "message": text,
                            "completed": [],
                            "messages": [{"role": "user", "content": text}] if text else [],
                            "task_results": {},
                            "long_term": facts,
                        },
                        config,
                    )
                else:
                    graph.update_state(config, {"long_term": facts})
                    graph.invoke(None, config)
                graph_state = graph.get_state(config)
                values = graph_state.values or {}
                finished = list(values.get("completed") or [])
                current = finished[-1] if finished else None
                nxt = tuple(graph_state.next or ())
                if not nxt:
                    status = "done"
                elif current == "draft" and "join" not in finished:
                    status = "awaiting_approval"
                else:
                    status = "running"
                readable = {
                    "message": values.get("message"),
                    "completed": finished,
                    "draft": values.get("draft"),
                    "final": values.get("final"),
                    "messages": values.get("messages") or [],
                    "task_results": values.get("task_results") or {},
                    "long_term": facts,
                }
                if receipt:
                    readable["receipt"] = receipt
                conn.execute(
                    """
                    UPDATE runs
                    SET status = %s,
                        current_node = %s,
                        state_json = %s,
                        updated_at = now(),
                        completed_at = CASE WHEN %s = 'done' THEN now() ELSE completed_at END
                    WHERE id = %s
                    """,
                    (status, current, Json(readable), status, run_id),
                )
                if status == "awaiting_approval":
                    self._ensure_approval(conn, run_id, str(readable.get("draft") or ""))
                taken += 1
                if status != "running":
                    fresh = self._lock(conn, run_id, tenant_id)
                    return _view(fresh)
            if max_steps is not None and taken >= max_steps:
                with conn.transaction():
                    return _view(self._lock(conn, run_id, tenant_id))

    def _send_approved(
        self,
        conn,
        *,
        run_id: str,
        tenant_id: str,
        draft: str,
        approving: bool,
    ) -> tuple[bool, str | None]:
        """Commit the receipt, then let the caller write the join checkpoint.

        The flag is false when this attempt stopped and the send node must not run.
        """
        approval = self._approval(conn, run_id)
        if approval is None:
            approval = self._commit_approval(run_id, draft)
        if approval["status"] not in {"pending", "approved"}:
            self._fail_effect(
                conn,
                run_id,
                approval["task_id"],
                EffectOutcome(disposition="failed", error_code="rejected", error_message="approval rejected"),
            )
            return False, None
        if approval["status"] == "pending" and not approving:
            self._mark_awaiting(conn, run_id)
            return False, None
        outcome = self._deliver(tenant_id, run_id, approval)
        if outcome.disposition == "unknown":
            self._block_unknown(conn, run_id, approval["task_id"], outcome)
            return False, None
        if outcome.disposition == "failed":
            self._fail_effect(conn, run_id, approval["task_id"], outcome)
            return False, None
        if outcome.disposition == "conflict":
            raise DurabilityError("action in progress", code="conflict")
        if outcome.disposition not in {"sent", "replayed"}:
            self._fail_effect(
                conn,
                run_id,
                approval["task_id"],
                EffectOutcome(
                    disposition="failed",
                    error_code=outcome.error_code or outcome.disposition,
                    error_message=outcome.error_message,
                ),
            )
            return False, None
        if outcome.disposition == "sent":
            self._audit_send(run_id, approval["task_id"], approval["idempotency_key"], outcome.result_ref or "")
            if self._after_effect is not None:
                self._after_effect(outcome)
        self._mark_task_done(conn, approval["task_id"])
        return True, outcome.result_ref

    def _deliver(self, tenant_id: str, run_id: str, approval: dict) -> EffectOutcome:
        if approval["status"] == "pending":
            self._mark_approved(str(approval["id"]))
        runtime = SkillRuntime(store=PostgresIdempotencyStore())
        runtime.register(SEND_EMAIL)
        runtime.register_handler(SEND_EMAIL.name, self._sender)
        payload = approval["payload_json"] if isinstance(approval["payload_json"], dict) else {}
        blocked = outbound_block("", str(payload.get("body") or ""))
        if blocked:
            return EffectOutcome(disposition="failed", error_code="outbound_blocked", error_message=blocked)
        return runtime.apply(
            SEND_EMAIL.name,
            {"body": str(payload.get("body") or ""), "idempotency_key": approval["idempotency_key"]},
            idempotency_key=str(approval["idempotency_key"]),
            tenant_id=tenant_id,
            run_id=run_id,
            approved=True,
        )

    def _long_term(self, conn, run_id: str, tenant_id: str) -> list[dict[str, Any]]:
        row = conn.execute(
            """
            SELECT sessions.user_id
            FROM runs
            JOIN sessions ON sessions.id = runs.session_id
            WHERE runs.id = %s AND runs.tenant_id = %s
            """,
            (run_id, tenant_id),
        ).fetchone()
        if row is None:
            return []
        return self._memory.recall_long_term(tenant_id=tenant_id, user_id=str(row["user_id"]))

    def _latest_snapshot(self, conn, run_id: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT state_snapshot
            FROM checkpoints
            WHERE run_id = %s
            ORDER BY step_seq DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None or not isinstance(row["state_snapshot"], dict):
            return {}
        return row["state_snapshot"]

    def _approval(self, conn, run_id: str) -> dict | None:
        return conn.execute(
            """
            SELECT id, task_id, status, idempotency_key, payload_json
            FROM approvals
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()

    def _ensure_approval(self, conn, run_id: str, draft: str) -> dict:
        found = self._approval(conn, run_id)
        if found:
            return found
        task_id = new_id("task")
        approval_id = new_id("appr")
        action_key = f"{run_id}:send_email"
        conn.execute(
            """
            INSERT INTO tasks (id, run_id, task_index, agent_name, args_json, status)
            VALUES (%s, %s, 0, '发邮件', %s, 'pending')
            """,
            (task_id, run_id, Json({"body": draft})),
        )
        conn.execute(
            """
            INSERT INTO approvals (
                id, run_id, task_id, action_type, payload_json, status, idempotency_key
            )
            VALUES (%s, %s, %s, 'send_email', %s, 'pending', %s)
            """,
            (
                approval_id,
                run_id,
                task_id,
                Json({"body": draft, "action": "send_email"}),
                action_key,
            ),
        )
        return {
            "id": approval_id,
            "task_id": task_id,
            "status": "pending",
            "idempotency_key": action_key,
            "payload_json": {"body": draft, "action": "send_email"},
        }

    def _commit_approval(self, run_id: str, draft: str) -> dict:
        side = connect()
        try:
            with side.transaction():
                approval = self._ensure_approval(side, run_id, draft)
            return approval
        finally:
            side.close()

    def _mark_approved(self, approval_id: str) -> None:
        side = connect()
        try:
            with side.transaction():
                side.execute(
                    """
                    UPDATE approvals
                    SET status = 'approved', decided_at = now(), decided_by = 'approval_callback'
                    WHERE id = %s AND status = 'pending'
                    """,
                    (approval_id,),
                )
        finally:
            side.close()

    def _mark_awaiting(self, conn, run_id: str) -> None:
        conn.execute(
            """
            UPDATE runs
            SET status = 'awaiting_approval', updated_at = now()
            WHERE id = %s
            """,
            (run_id,),
        )

    def _mark_task_done(self, conn, task_id: str) -> None:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'done', finished_at = now(), error_code = NULL, error_message = NULL
            WHERE id = %s
            """,
            (task_id,),
        )

    def _fail_effect(self, conn, run_id: str, task_id: str, outcome: EffectOutcome) -> None:
        self._merge_state(
            conn,
            run_id,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
        )
        conn.execute(
            """
            UPDATE runs
            SET status = 'failed', updated_at = now(), completed_at = now()
            WHERE id = %s
            """,
            (run_id,),
        )
        conn.execute(
            """
            UPDATE tasks
            SET status = 'failed', error_code = %s, error_message = %s, finished_at = now()
            WHERE id = %s
            """,
            (outcome.error_code, outcome.error_message, task_id),
        )

    def _block_unknown(self, conn, run_id: str, task_id: str, outcome: EffectOutcome) -> None:
        self._merge_state(
            conn,
            run_id,
            blocked_reason="effect_unknown",
            error_code=outcome.error_code,
            error_message=outcome.error_message,
        )
        conn.execute(
            "UPDATE runs SET status = 'suspended', updated_at = now() WHERE id = %s",
            (run_id,),
        )
        conn.execute(
            """
            UPDATE tasks
            SET status = 'failed', error_code = %s, error_message = %s, finished_at = now()
            WHERE id = %s
            """,
            (outcome.error_code or "effect_unknown", outcome.error_message, task_id),
        )

    def _merge_state(self, conn, run_id: str, **fields: Any) -> None:
        row = conn.execute("SELECT state_json FROM runs WHERE id = %s", (run_id,)).fetchone()
        state = dict(row["state_json"] or {}) if row and isinstance(row["state_json"], dict) else {}
        state.update(fields)
        conn.execute(
            "UPDATE runs SET state_json = %s, updated_at = now() WHERE id = %s",
            (Json(state), run_id),
        )

    def _audit_send(self, run_id: str, task_id: str, action_key: str, result_ref: str) -> None:
        side = connect()
        try:
            with side.transaction():
                side.execute(
                    """
                    INSERT INTO audit_log (id, run_id, task_id, actor, action, payload_json)
                    VALUES (%s, %s, %s, 'system', 'send_email', %s)
                    """,
                    (
                        new_id("audit"),
                        run_id,
                        task_id,
                        Json({"result_ref": result_ref, "idempotency_key": action_key}),
                    ),
                )
        finally:
            side.close()

    def _by_key(self, conn, tenant_id: str, idempotency_key: str) -> dict | None:
        return conn.execute(
            """
            SELECT id, status, current_node, user_input
            FROM runs
            WHERE tenant_id = %s AND idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()

    def _ensure_run(
        self,
        conn,
        *,
        run_id: str,
        tenant_id: str,
        user_id: str,
        user_input: str,
        idempotency_key: str,
        session_id: str,
    ) -> None:
        found = conn.execute("SELECT id, tenant_id FROM runs WHERE id = %s", (run_id,)).fetchone()
        if found:
            if found["tenant_id"] != tenant_id:
                raise DurabilityError("run not found", code="not_found")
            return
        conn.execute(
            """
            INSERT INTO sessions (id, user_id, tenant_id, channel, status)
            VALUES (%s, %s, %s, 'api', 'active')
            ON CONFLICT (id) DO NOTHING
            """,
            (session_id, user_id, tenant_id),
        )
        conn.execute(
            """
            INSERT INTO runs (id, session_id, tenant_id, user_input, status, idempotency_key)
            VALUES (%s, %s, %s, %s, 'running', %s)
            """,
            (run_id, session_id, tenant_id, user_input, idempotency_key),
        )

    def _lock(self, conn, run_id: str, tenant_id: str) -> dict:
        # FOR UPDATE deadlocks with the action-key insert: that row references runs,
        # and the foreign-key check takes FOR KEY SHARE. NO KEY UPDATE still blocks
        # a second worker, and it does not block that check.
        row = conn.execute(
            """
            SELECT id, tenant_id, status, current_node, state_json
            FROM runs
            WHERE id = %s
            FOR NO KEY UPDATE
            """,
            (run_id,),
        ).fetchone()
        if row is None or row["tenant_id"] != tenant_id:
            raise DurabilityError("run not found", code="not_found")
        return row

    def _set_status(self, run_id: str, tenant_id: str, allowed: set[str], status: str) -> dict[str, str]:
        conn = connect()
        try:
            migrate(conn)
            with conn.transaction():
                row = self._lock(conn, run_id, tenant_id)
                if row["status"] in {"done", "failed"}:
                    return _view(row)
                if row["status"] not in allowed:
                    raise DurabilityError("run cannot change status", code="conflict")
                conn.execute(
                    "UPDATE runs SET status = %s, updated_at = now() WHERE id = %s",
                    (status, run_id),
                )
                row = self._lock(conn, run_id, tenant_id)
                return _view(row)
        finally:
            conn.close()


def _effect_unknown(row: dict) -> bool:
    state = row.get("state_json") or {}
    return isinstance(state, dict) and state.get("blocked_reason") == "effect_unknown"


def _view(row: dict) -> dict[str, str]:
    return {
        "run_id": row["id"],
        "status": row["status"],
        "current_node": row["current_node"] or "",
    }
