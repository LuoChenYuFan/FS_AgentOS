"""Layer 6: scenarios A, B, and C, including a condition that does not fire."""

import time

import pytest
from fs_agent_runtime.agents import AgentRunner
from fs_agent_runtime.planner import RulePlanner
from fs_agent_runtime.registry import CatalogError, load_catalog, validate_catalog
from fs_common.contracts.dag import TaskDag, TaskNode
from fs_orchestrator.schedule import CollaborationService, validate_dag
from fs_skill_runtime import SkillRuntime
from fs_skill_sdk import SkillSpec

_WEATHER_MAIL = "帮我查一下天津明天的天气，然后将天气情况以邮件的形式发给小明"
_PARALLEL = "帮我规划一下上海的旅游攻略，顺便给张三发邮件告诉他，让他明天去厦门出差"
_HOTEL_MAIL = "帮我查询下广州5星级酒店的情况，如果明晚平均价位低于2000，就给李四发邮件"


class SequencePlanner:
    def __init__(self, dags: list[TaskDag]) -> None:
        self.dags = dags
        self.calls = 0

    def plan(self, text: str, *, attempt: int, errors: list[str]) -> TaskDag:
        del text, attempt, errors
        dag = self.dags[min(self.calls, len(self.dags) - 1)]
        self.calls += 1
        return dag


def _fixed_weather(arguments: dict[str, object]) -> dict[str, object]:
    del arguments
    return {"summary": "晴, 26℃", "temp": 26, "condition": "晴"}


class ScriptedAgents:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = outputs
        self.calls = 0

    def execute(
        self, *, agent: object, args: dict[str, object], skill: str | None
    ) -> dict[str, object]:
        del agent, args, skill
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


def test_catalog_loads_and_rejects_a_wobbly_directory() -> None:
    catalog = load_catalog()
    assert catalog.agent("发邮件") is not None
    assert catalog.agent("发邮件").needs_approval is True
    assert catalog.agent("发邮件").side_effect == "irreversible"
    assert catalog.agent("发邮件").allowed_skills == ["发邮件"]
    wobble = catalog.model_copy(deep=True)
    wobble.agents[1].when = wobble.agents[0].when
    assert any("same when" in item for item in validate_catalog(wobble))
    unknown = catalog.model_copy(deep=True)
    unknown.agents[0].allowed_skills = ["不存在的技能"]
    assert any("unknown skills" in item for item in validate_catalog(unknown))
    open_gate = catalog.model_copy(deep=True)
    mail = open_gate.agent("发邮件")
    assert mail is not None
    mail.side_effect = "irreversible"
    mail.needs_approval = False
    assert any("needs approval" in item for item in validate_catalog(open_gate))


def test_a_weather_question_returns_the_forecast_to_the_user() -> None:
    outcome = CollaborationService(
        agents=AgentRunner(weather=_fixed_weather)
    ).execute_outcome(
        task_id="run_weather_reply",
        payload={"text": "帮我查一下天津明天的天气", "tenant_id": "tenant_a"},
    )
    assert outcome.status == "done"
    assert outcome.reply == "晴, 26℃"


def test_a_date_past_ten_days_is_explained_to_the_user() -> None:
    outcome = CollaborationService().execute_outcome(
        task_id="run_far_weather",
        payload={"text": "帮我查30天后北京的天气", "tenant_id": "tenant_a"},
    )
    assert outcome.status == "done"
    assert outcome.reply == "查询的时间比较长，暂时只支持查询10天内的天气情况"
    assert outcome.delivered[1]["日期"] == "30天后"


def test_a_single_request_is_one_node() -> None:
    dag = RulePlanner().plan("帮我查一下天津明天的天气", attempt=1, errors=[])
    assert len(dag.tasks) == 1
    assert dag.tasks[0].agent == "聊天助手"
    assert dag.tasks[0].deps == []


def test_weather_keeps_hong_kong_instead_of_a_default_city() -> None:
    dag = RulePlanner().plan("帮我查询香港明天的天气", attempt=1, errors=[])
    assert dag.tasks[0].args["城市"] == "香港"
    assert dag.tasks[0].args["日期"] == "明天"


def test_mail_to_zhang_stays_email_and_a_fact_lookup_adds_retrieval() -> None:
    mailed = RulePlanner().plan("给张博洋发邮件", attempt=1, errors=[])
    assert [task.agent for task in mailed.tasks] == ["发邮件"]
    assert mailed.tasks[0].args["skill"] == "发邮件"
    assert mailed.tasks[0].args["收件人"] == "张博洋"

    looked_up = RulePlanner().plan("查出张博洋的电话再发邮件给他", attempt=1, errors=[])
    assert [task.agent for task in looked_up.tasks] == ["RAG Agent", "发邮件"]
    assert looked_up.tasks[1].deps == [1]


def test_owner_mail_uses_the_user_message_as_the_body() -> None:
    dag = RulePlanner().plan(
        "给你的主人发一封邮件，告诉他我明天调休去上海", attempt=1, errors=[]
    )
    assert dag.tasks[0].agent == "发邮件"
    assert dag.tasks[0].args["收件人"] == "你的主人"
    assert "调休" in str(dag.tasks[0].args["正文"])


def test_mail_without_body_asks_for_content_and_does_not_wait() -> None:
    outcome = CollaborationService().execute_outcome(
        task_id="run_empty_mail",
        payload={"text": "发邮件", "tenant_id": "tenant_a"},
    )
    assert outcome.status == "done"
    assert outcome.pending_approval is None
    assert "还没有邮件内容" in outcome.reply


def test_mail_with_body_waits_and_cancel_clears_the_draft() -> None:
    sent: list[dict[str, object]] = []
    service = CollaborationService(sender=_recording_sender(sent))
    outcome = service.execute_outcome(
        task_id="run_confirm_mail",
        payload={"text": "给你的主人发邮件，告诉他我明天调休", "tenant_id": "tenant_a"},
    )
    assert outcome.status == "awaiting_approval"
    assert "调休" in outcome.reply
    assert "请确认是否发送" in outcome.reply
    cancelled = service.cancel("run_confirm_mail", tenant_id="tenant_a")
    assert cancelled["status"] == "cancelled"
    assert "已取消" in cancelled["reply"]
    assert sent == []


def _recording_sender(bucket: list[dict[str, object]]):
    def sender(arguments: dict[str, object]) -> str:
        bucket.append(dict(arguments))
        return "已发送邮件"

    return sender


def test_scenario_a_injects_the_weather_and_waits_for_mail_approval() -> None:
    agents = AgentRunner(weather=_fixed_weather)
    sent: list[dict[str, object]] = []
    service = CollaborationService(agents=agents, sender=_recording_sender(sent))
    outcome = service.execute_outcome(
        task_id="run_a",
        payload={
            "text": _WEATHER_MAIL,
            "tenant_id": "tenant_a",
            "idempotency_key": "idem_run",
        },
    )
    assert outcome.status == "awaiting_approval"
    assert outcome.pending_approval is not None
    assert "晴, 26℃" in outcome.reply
    assert "请确认是否发送" in outcome.reply
    assert "天津明天 晴, 26℃" in str(outcome.delivered[2]["正文"])
    assert sent == []
    approved = service.approve("run_a", tenant_id="tenant_a")
    assert approved["status"] == "done"
    assert "已发送邮件" in approved["reply"]
    assert sent and "晴, 26℃" in str(sent[0].get("正文") or sent[0].get("body") or "")
    assert len(agents.seen) == 2


def _fixed_itinerary(arguments: dict[str, object]) -> dict[str, object]:
    return {
        "summary": "上海一日",
        "itinerary": {"city": arguments.get("城市", ""), "days": 1, "places": []},
        "cost_estimate": 1000,
    }


def test_scenario_b_keeps_the_parallel_email_unaware_of_the_itinerary() -> None:
    agents = AgentRunner(itinerary=_fixed_itinerary)
    sent: list[dict[str, object]] = []
    service = CollaborationService(agents=agents, sender=_recording_sender(sent))
    outcome = service.execute_outcome(
        task_id="run_b",
        payload={"text": _PARALLEL, "tenant_id": "tenant_a"},
    )
    assert outcome.status == "awaiting_approval"
    assert "上海一日" in outcome.reply
    assert "请确认是否发送" in outcome.reply
    assert "上海" not in str(outcome.delivered[2].get("正文") or "")
    assert "itinerary" not in outcome.delivered[2]
    assert outcome.delivered[2]["收件人"] == "张三"
    approved = service.approve("run_b", tenant_id="tenant_a")
    assert approved["status"] == "done"
    assert sent


def test_scenario_c_sends_only_when_the_price_is_under_the_threshold() -> None:
    cheap = AgentRunner(hotel_price=1850)
    sent: list[dict[str, object]] = []
    service = CollaborationService(agents=cheap, sender=_recording_sender(sent))
    waiting = service.execute_outcome(
        task_id="run_c",
        payload={"text": _HOTEL_MAIL, "tenant_id": "tenant_a"},
    )
    assert waiting.status == "awaiting_approval"
    assert "请确认是否发送" in waiting.reply
    assert "广州明晚五星均价 1850.0 元" in str(waiting.delivered[2]["正文"])
    approved = service.approve("run_c", tenant_id="tenant_a")
    assert approved["status"] == "done"
    assert sent

    expensive = AgentRunner(hotel_price=2200)
    skipped = CollaborationService(agents=expensive).execute_outcome(
        task_id="run_c_skip",
        payload={"text": _HOTEL_MAIL, "tenant_id": "tenant_a"},
    )
    assert skipped.status == "done"
    assert skipped.reply == "查到均价 2200 元，未低于 2000，邮件未发送"
    assert skipped.pending_approval is None
    assert len(expensive.seen) == 1

    raised = AgentRunner(hotel_price=2200)
    still_sent: list[dict[str, object]] = []
    still_service = CollaborationService(
        agents=raised, sender=_recording_sender(still_sent)
    )
    still = still_service.execute_outcome(
        task_id="run_c_3000",
        payload={"text": _HOTEL_MAIL.replace("2000", "3000"), "tenant_id": "tenant_a"},
    )
    assert still.status == "awaiting_approval"
    assert still_service.approve("run_c_3000", tenant_id="tenant_a")["status"] == "done"
    assert still_sent


def test_a_bad_plan_is_retried_three_times_then_degrades() -> None:
    bad = TaskDag(
        run_id="bad",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查天气"},
                deps=[2],
                output_key="weather",
            ),
            TaskNode(
                id=2,
                agent="发邮件",
                deps=[1],
                args={"收件人": "小明", "正文模板": "你好"},
            ),
        ],
    )
    good = TaskDag(
        run_id="good",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查天气", "城市": "天津"},
                output_key="weather",
            )
        ],
    )
    healed = SequencePlanner([bad, bad, good])
    outcome = CollaborationService(
        planner=healed, agents=AgentRunner(weather=_fixed_weather)
    ).execute_outcome(
        task_id="run_plan",
        payload={"text": "随便", "tenant_id": "tenant_a"},
    )
    assert healed.calls == 3
    assert outcome.plan_attempts == 3
    assert outcome.status == "done"

    stuck = SequencePlanner([bad, bad, bad])
    degraded = CollaborationService(planner=stuck).execute_outcome(
        task_id="run_stuck",
        payload={"text": "随便", "tenant_id": "tenant_a"},
    )
    assert stuck.calls == 3
    assert degraded.status == "degraded"
    assert degraded.reply == "这次安排没有通过校验，请换一种说法再试一次。"


def test_joiner_replans_twice_then_degrades() -> None:
    dag = TaskDag(
        run_id="weather",
        tasks=[
            TaskNode(
                id=1, agent="聊天助手", args={"skill": "查天气"}, output_key="weather"
            )
        ],
    )
    planner = SequencePlanner([dag])
    recovering = ScriptedAgents([{}, {"summary": "晴, 26℃", "temp": 26}])
    outcome = CollaborationService(planner=planner, agents=recovering).execute_outcome(
        task_id="run_join",
        payload={"text": "帮我查一下天津明天的天气", "tenant_id": "tenant_a"},
    )
    assert recovering.calls == 2
    assert outcome.replan_count == 1
    assert outcome.status == "done"

    empty = ScriptedAgents([{}])
    failed = CollaborationService(
        planner=SequencePlanner([dag]), agents=empty
    ).execute_outcome(
        task_id="run_join_fail",
        payload={"text": "帮我查一下天津明天的天气", "tenant_id": "tenant_a"},
    )
    assert empty.calls == 3
    assert failed.replan_count == 2
    assert failed.status == "degraded"
    assert failed.reply == "这次安排没有做完，请换一种说法再试一次。"


class RecordingKnowledge:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query(
        self, *, query: str, tenant_id: str, top_k: int, scope_filter: str
    ) -> dict[str, object]:
        self.calls.append(
            {
                "query": query,
                "tenant_id": tenant_id,
                "top_k": top_k,
                "scope_filter": scope_filter,
            }
        )
        return {
            "chunks": ["差旅住宿不超过800元"],
            "sources": ["s3://fs-documents/policy"],
            "confidence": 0.8,
            "retrieved_context": {
                "role": "data",
                "query": query,
                "citations": [{"text": "差旅住宿不超过800元"}],
            },
        }


def test_the_orchestrator_queries_knowledge_before_the_hotel_task() -> None:
    agents = AgentRunner()
    knowledge = RecordingKnowledge()
    text = "按公司差旅标准查一下广州的酒店"
    outcome = CollaborationService(agents=agents, knowledge=knowledge).execute_outcome(
        task_id="run_rag",
        payload={"text": text, "tenant_id": "tenant_a", "knowledge_scope": "hr"},
    )
    assert outcome.status == "done"
    assert knowledge.calls == [
        {"query": text, "tenant_id": "tenant_a", "top_k": 5, "scope_filter": "hr"}
    ]
    found = outcome.task_results["knowledge"]
    assert found["chunks"] == ["差旅住宿不超过800元"]
    assert found["retrieved_context"]["role"] == "data"
    assert len(agents.seen) == 1
    assert agents.seen[0]["城市"] == "广州"


def test_chat_and_itinerary_agents_cannot_call_rag_query() -> None:
    catalog = load_catalog()
    chat = catalog.agent("聊天助手")
    travel = catalog.agent("行程规划")
    reader = catalog.agent("RAG Agent")
    assert chat is not None and "rag_query" not in chat.allowed_skills
    assert travel is not None and "rag_query" not in travel.allowed_skills
    assert reader is not None and reader.allowed_skills == ["rag_query"]
    denied = TaskDag(
        run_id="denied",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "rag_query"},
                output_key="knowledge",
            )
        ],
    )
    assert any("allowed_skills" in item for item in validate_dag(denied, catalog))


_BEIJING = "帮我查询明天北京天气，并规划一下从西直门地铁站到北京南站乘车路线，不能超过公司差旅费"


class RememberingPlanner:
    def __init__(self, dags: list[TaskDag]) -> None:
        self.dags = dags
        self.calls = 0
        self.errors: list[list[str]] = []

    def plan(self, text: str, *, attempt: int, errors: list[str]) -> TaskDag:
        del text, attempt
        self.errors.append(list(errors))
        dag = self.dags[min(self.calls, len(self.dags) - 1)]
        self.calls += 1
        return dag


def test_a_short_plan_is_sent_back_until_the_route_gap_is_included() -> None:
    weather_only = TaskDag(
        run_id="short",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查天气", "城市": "北京", "日期": "明天"},
                output_key="weather",
            )
        ],
    )
    complete = TaskDag(
        run_id="complete",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查天气", "城市": "北京", "日期": "明天"},
                output_key="weather",
            ),
            TaskNode(
                id=2,
                agent="聊天助手",
                args={
                    "skill": "查路线",
                    "出发地": "西直门地铁站",
                    "目的地": "北京南站",
                },
                output_key="route",
            ),
            TaskNode(
                id=3,
                agent="RAG Agent",
                args={"skill": "rag_query", "query": "公司差旅标准", "top_k": 5},
                output_key="knowledge",
            ),
            TaskNode(
                id=4,
                agent="计算",
                args={"skill": "amount_from_text", "text": "${{knowledge.chunks}}"},
                deps=[3],
                output_key="cap",
            ),
            TaskNode(
                id=5,
                agent="计算",
                args={
                    "skill": "subtract",
                    "left": "${{route.fare}}",
                    "right": "${{cap.amount}}",
                },
                deps=[2, 4],
                output_key="gap",
            ),
        ],
    )
    planner = RememberingPlanner([weather_only, complete])
    outcome = CollaborationService(
        planner=planner,
        agents=AgentRunner(weather=_fixed_weather),
        knowledge=RecordingKnowledge(),
    ).execute_outcome(
        task_id="run_beijing", payload={"text": _BEIJING, "tenant_id": "tenant_a"}
    )
    assert planner.calls == 2
    assert any("查路线" in item for item in planner.errors[1])
    assert any("subtract" in item for item in planner.errors[1])
    assert outcome.status == "done"
    assert outcome.delivered[2]["起点"] == "西直门地铁站"
    assert outcome.delivered[2]["终点"] == "北京南站"
    assert "出发地" not in outcome.delivered[2]
    assert outcome.task_results["gap"]["value"] == -765
    assert outcome.reply == "比标准低 765 元"


def test_skill_runtime_rejects_a_bad_argument_and_a_timeout() -> None:
    runtime = SkillRuntime()
    runtime.register(
        SkillSpec(name="查天气", input_schema={"城市": "string"}, timeout_sec=30)
    )
    with pytest.raises(ValueError, match="城市"):
        runtime.call("查天气", {"城市": 12}, lambda arguments: {"summary": "晴"})
    runtime.register(SkillSpec(name="查路线", timeout_sec=1))
    with pytest.raises(ValueError, match="timed out"):
        runtime.call("查路线", {}, lambda arguments: time.sleep(2))


def test_catalog_error_mentions_every_problem() -> None:
    try:
        raise CatalogError(["agent names must be unique", "skill names must be unique"])
    except CatalogError as exc:
        assert "agent names" in str(exc)
        assert exc.errors[1].startswith("skill")
