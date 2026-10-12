"""Chat model plans a task list. Intent model classifies. Subtraction stays in code."""

import json
import os

import httpx
import pytest
from fs_agent_runtime.agents import AgentRunner
from fs_common.contracts.dag import TaskDag, TaskNode
from fs_common.contracts.intent import IntentLayer
from fs_intent_funnel import (
    FunnelConfig,
    IntentFunnel,
    MemoryIntentIndex,
    StructuredIntentModel,
)
from fs_intent_funnel.models import IntentSubmission
from fs_intent_funnel.observe import (
    EVAL_UTTERANCES,
    failure_reason_counts,
    measure_first_try,
    reasons_over_count,
)
from fs_common.errors import ModelQuotaExhausted
from fs_llm_gateway import DashScopeChat, dashscope_chat_from_env
from fs_orchestrator.chat_planner import ChatPlanner
from fs_orchestrator.postgres import load_local_env
from fs_orchestrator.schedule import CollaborationService


class ScriptChat:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        assert messages[0]["role"] == "system"
        return self.text


def test_a_wrapped_json_guess_is_cleaned_before_validation() -> None:
    dirty = """
    好的，结果如下：
    ```json
    {"intent":"weather","confidence":0.92,"second_intent":null,}
    ```
    请查收！！
    """
    guess = StructuredIntentModel(ScriptChat(dirty)).classify("帮我查询明天北京天气")
    assert guess.intent == "weather"
    assert guess.confidence == 0.92


def test_intent_model_reads_the_json_guess() -> None:
    chat = ScriptChat('{"intent":"knowledge","confidence":0.91,"second_intent":null}')
    guess = StructuredIntentModel(chat).classify("年假有几天")
    assert guess.intent == "knowledge"
    assert guess.confidence == 0.91
    assert guess.second_intent is None


class RetryChat:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[str] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        self.seen.append(messages[1]["content"])
        if self.calls == 1:
            return "我先用中文说说"
        return '{"intent":"task","confidence":0.9,"second_intent":"knowledge"}'


def test_intent_model_asks_again_when_the_answer_is_not_json() -> None:
    chat = RetryChat()
    guess = StructuredIntentModel(chat).classify("查天气并规划路线")
    assert chat.calls == 2
    assert "没有找到 JSON 对象" in chat.seen[1]
    assert guess.intent == "task"
    assert guess.second_intent == "knowledge"


def test_a_chinese_intent_label_is_mapped() -> None:
    chat = ScriptChat(
        '{"intent":"出行规划","confidence":0.86,"second_intent":"知识库"}'
    )
    guess = StructuredIntentModel(chat).classify("查天气并对照差旅")
    assert chat.calls == 1
    assert guess.intent == "task"
    assert guess.second_intent == "knowledge"
    assert guess.confidence == 0.86


class SchemaChat:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.schemas: list[object] = []

    def complete_structured(
        self, messages: list[dict[str, str]], schema: object
    ) -> dict[str, object]:
        del messages
        self.schemas.append(schema)
        return self.payload


def test_intent_model_validates_the_schema_submission() -> None:
    chat = SchemaChat({"intent": "天气", "confidence": 0.93, "second_intent": None})
    guess = StructuredIntentModel(chat).classify("明天广州的天气怎么样")
    assert chat.schemas == [IntentSubmission]
    assert guess.intent == "weather"
    assert guess.confidence == 0.93


def test_a_schema_submission_outside_the_range_is_sent_back() -> None:
    class Once:
        def __init__(self) -> None:
            self.calls = 0

        def complete_structured(
            self, messages: list[dict[str, str]], schema: object
        ) -> dict[str, object]:
            del schema
            self.calls += 1
            assert "超出 0 到 1" in messages[1]["content"] or self.calls == 1
            if self.calls == 1:
                return {"intent": "weather", "confidence": 9}
            return {"intent": "weather", "confidence": 0.8}

    chat = Once()
    guess = StructuredIntentModel(chat).classify("明天广州的天气怎么样")
    assert chat.calls == 2
    assert guess.intent == "weather"
    assert guess.confidence == 0.8


def test_a_legal_but_low_confidence_is_a_business_rejection() -> None:
    chat = SchemaChat({"intent": "weather", "confidence": 0.4, "second_intent": None})
    guess = StructuredIntentModel(chat).classify("帮我查询明天北京天气")
    assert chat.schemas == [IntentSubmission, IntentSubmission]
    assert IntentSubmission(intent="weather", confidence=0.4).confidence == 0.4
    assert guess.intent == "weather"
    assert guess.confidence == 0.4
    assert guess.failure_reason == "business_confidence"


def test_low_confidence_is_told_to_the_model_before_the_second_try() -> None:
    class LowThenClear:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def complete_structured(
            self, messages: list[dict[str, str]], schema: object
        ) -> dict[str, object]:
            del schema
            self.seen.append(messages[1]["content"])
            if len(self.seen) == 1:
                return {"intent": "weather", "confidence": 0.4, "second_intent": None}
            return {"intent": "weather", "confidence": 0.91, "second_intent": None}

    chat = LowThenClear()
    guess = StructuredIntentModel(chat).classify("帮我查询明天北京天气")
    assert "confidence 0.4 低于 0.7" in chat.seen[1]
    assert guess.failure_reason == ""
    assert guess.confidence == 0.91


def test_a_single_quoted_object_is_described_as_a_parse_error() -> None:
    chat = ScriptChat("{'intent': 'weather', 'confidence': 0.9}")
    guess = StructuredIntentModel(chat).classify("帮我查询明天北京天气")
    assert chat.calls == 2
    assert guess.failure_reason == "parse_error"


def test_confidence_on_the_business_line_is_accepted() -> None:
    chat = SchemaChat({"intent": "weather", "confidence": 0.7, "second_intent": None})
    guess = StructuredIntentModel(chat).classify("帮我查询明天北京天气")
    assert guess.failure_reason == ""
    assert guess.confidence == 0.7


def test_a_business_rejection_stays_on_the_large_model_attempt() -> None:
    chat = SchemaChat({"intent": "weather", "confidence": 0.4, "second_intent": None})
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=StructuredIntentModel(chat),
    )
    result = funnel.decide("帮我查询明天北京天气")
    attempt = result.attempts[-1]
    assert attempt.layer is IntentLayer.LARGE_LLM
    assert attempt.passed is False
    assert attempt.score == 0.4
    assert attempt.failure_reason == "business_confidence"
    assert result.decision.passed is False


def test_an_unknown_intent_label_is_rejected_instead_of_becoming_a_task() -> None:
    class Chat:
        def __init__(self) -> None:
            self.calls = 0
            self.seen: list[str] = []

        def complete(self, messages: list[dict[str, str]]) -> str:
            self.calls += 1
            self.seen.append(messages[1]["content"])
            return '{"intent":"飞船","confidence":0.9,"second_intent":null}'

    chat = Chat()
    model = StructuredIntentModel(chat)
    guess = model.classify("带我去火星")
    assert chat.calls == 2
    assert "intent 不在规定取值里" in chat.seen[1]
    assert guess.intent == "unknown"
    assert guess.failure_reason == "schema_error"
    assert model.last_tries == ["schema_error", "schema_error"]


def test_an_unknown_second_intent_is_rejected() -> None:
    class Chat:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def complete(self, messages: list[dict[str, str]]) -> str:
            self.seen.append(messages[1]["content"])
            if len(self.seen) == 1:
                return '{"intent":"task","confidence":0.9,"second_intent":"飞船"}'
            return '{"intent":"task","confidence":0.9,"second_intent":null}'

    chat = Chat()
    guess = StructuredIntentModel(chat).classify("办一件事")
    assert "second_intent 不在规定取值里" in chat.seen[1]
    assert guess.intent == "task"
    assert guess.failure_reason == ""


def test_a_model_timeout_stops_as_a_model_error() -> None:
    class Down:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: list[dict[str, str]]) -> str:
            del messages
            self.calls += 1
            raise RuntimeError("timeout")

    chat = Down()
    model = StructuredIntentModel(chat)
    guess = model.classify("你好")
    assert chat.calls == 2
    assert guess.failure_reason == "model_error"
    assert model.last_tries == ["model_error", "model_error"]


def test_the_fixed_sentences_measure_a_valid_first_reply() -> None:
    chat = ScriptChat('{"intent":"weather","confidence":0.91,"second_intent":null}')
    rate = measure_first_try(StructuredIntentModel(chat))
    assert rate == 1.0
    assert chat.calls == len(EVAL_UTTERANCES)


def test_failure_counts_ignore_successes_and_flag_a_spike() -> None:
    counts = failure_reason_counts(["", "parse_error", "parse_error", "model_error"])
    assert counts == {"parse_error": 2, "model_error": 1}
    assert reasons_over_count(counts, limit=2) == ["parse_error"]


def test_a_mixed_request_is_not_labeled_as_pure_knowledge() -> None:
    chat = ScriptChat('{"intent":"task","confidence":0.88,"second_intent":"knowledge"}')
    guess = StructuredIntentModel(chat).classify("按差旅标准查酒店并发邮件")
    assert guess.intent == "task"
    assert guess.second_intent == "knowledge"


def test_subtraction_uses_the_hotel_price_and_the_policy_amount() -> None:
    dag = TaskDag(
        run_id="plan",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查酒店", "城市": "天津", "星级": 4, "日期": "明天"},
                output_key="hotel",
            ),
            TaskNode(
                id=2,
                agent="计算",
                args={"skill": "amount_from_text", "text": "差旅报销标准不超过800元"},
                output_key="cap",
            ),
            TaskNode(
                id=3,
                agent="计算",
                args={
                    "skill": "subtract",
                    "left": "${{hotel.avg_price}}",
                    "right": "${{cap.amount}}",
                },
                deps=[1, 2],
                output_key="gap",
            ),
        ],
    )

    class Planner:
        def plan(self, text: str, *, attempt: int, errors: list[str]) -> TaskDag:
            del text, attempt, errors
            return dag

    outcome = CollaborationService(
        planner=Planner(), agents=AgentRunner()
    ).execute_outcome(
        task_id="run_gap",
        payload={"text": "比较房价和标准", "tenant_id": "tenant_a"},
    )
    assert outcome.status == "done"
    assert outcome.task_results["gap"]["value"] == 1050
    assert outcome.reply == "超了 1050 元"


def test_chat_planner_turns_model_json_into_a_dag() -> None:
    raw = """
    {"tasks":[
      {"id":1,"agent":"聊天助手","args":{"skill":"查天气","城市":"上海","日期":"明天"},"deps":[],"output_key":"weather","condition":null},
      {"id":2,"agent":"聊天助手","args":{"skill":"查路线","城市":"上海"},"deps":[],"output_key":"route","condition":null},
      {"id":3,"agent":"RAG Agent","args":{"skill":"rag_query","query":"公司差旅标准","top_k":5},"deps":[],"output_key":"knowledge","condition":null}
    ]}
    """
    dag = ChatPlanner(ScriptChat(raw)).plan(
        "查天气并规划路线，不能超过公司差旅标准", attempt=1, errors=[]
    )
    assert [task.agent for task in dag.tasks] == ["聊天助手", "聊天助手", "RAG Agent"]
    assert dag.tasks[2].args["skill"] == "rag_query"


def test_chat_planner_keeps_hong_kong_when_the_model_copies_shanghai() -> None:
    raw = """
    {"tasks":[
      {"id":1,"agent":"聊天助手","args":{"skill":"查天气","城市":"上海","日期":"明天"},"deps":[],"output_key":"weather","condition":null}
    ]}
    """
    dag = ChatPlanner(ScriptChat(raw)).plan("帮我查询香港明天的天气", attempt=1, errors=[])
    assert dag.tasks[0].args["城市"] == "香港"
    assert dag.tasks[0].args["日期"] == "明天"


class _ChatResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


def test_dashscope_chat_sends_the_pydantic_schema_as_a_function() -> None:
    def post(url: str, **kwargs: object) -> _ChatResponse:
        assert url.endswith("/chat/completions")
        body = kwargs["json"]
        assert isinstance(body, dict)
        tool = body["tools"][0]["function"]
        assert tool["name"] == "submit_intent"
        assert "weather" in json.dumps(tool["parameters"])
        assert body["tool_choice"]["function"]["name"] == "submit_intent"
        arguments = '{"intent":"weather","confidence":0.9,"second_intent":null}'
        return _ToolResponse(arguments)

    chat = DashScopeChat(
        api_key="test-key", workspace_id="workspace", model="qwen3.8-max", post=post
    )  # type: ignore[arg-type]
    found = chat.complete_structured(
        [{"role": "user", "content": "明天广州的天气怎么样"}], IntentSubmission
    )
    assert found["intent"] == "weather"
    assert found["confidence"] == 0.9


class _ToolResponse:
    def __init__(self, arguments: str) -> None:
        self._arguments = arguments

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "submit_intent",
                                    "arguments": self._arguments,
                                },
                            }
                        ],
                    }
                }
            ]
        }


def test_dashscope_chat_cleans_a_trailing_comma_inside_the_function_call() -> None:
    arguments = '好的：\n```json\n{"intent":"weather","confidence":0.9,"second_intent":null,}\n```'

    def post(url: str, **kwargs: object) -> _ToolResponse:
        del url, kwargs
        return _ToolResponse(arguments)

    chat = DashScopeChat(
        api_key="test-key", workspace_id="workspace", model="qwen3.8-max", post=post
    )  # type: ignore[arg-type]
    found = chat.complete_structured(
        [{"role": "user", "content": "帮我查询明天北京天气"}], IntentSubmission
    )
    assert found["intent"] == "weather"


def test_dashscope_chat_reads_the_message_content() -> None:
    def post(url: str, **kwargs: object) -> _ChatResponse:
        assert url.endswith("/chat/completions")
        body = kwargs["json"]
        assert isinstance(body, dict)
        assert body["model"] == "qwen3.7-plus"
        return _ChatResponse("ok")

    chat = DashScopeChat(
        api_key="test-key", workspace_id="workspace", model="qwen3.7-plus", post=post
    )  # type: ignore[arg-type]
    assert chat.complete([{"role": "user", "content": "只回复ok"}]) == "ok"


def test_dashscope_chat_reports_an_exhausted_free_quota() -> None:
    def post(url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "insufficient_quota",
                    "message": "Free quota exhausted.",
                }
            },
            request=httpx.Request("POST", url),
        )

    chat = DashScopeChat(
        api_key="test-key", workspace_id="workspace", model="qwen3.7-plus", post=post
    )  # type: ignore[arg-type]
    with pytest.raises(ModelQuotaExhausted, match="qwen3.7-plus模型额度已用光，暂时无法使用该功能"):
        chat.complete([{"role": "user", "content": "年假有几天"}])


def _models_configured() -> bool:
    load_local_env()
    return bool(
        os.environ.get("DASHSCOPE_API_KEY")
        and os.environ.get("CHAT_MODEL")
        and os.environ.get("INTENT_MODEL")
    )


@pytest.mark.skipif(
    not _models_configured(), reason="chat and intent models are not configured"
)
def test_live_chat_model_answers() -> None:
    chat = dashscope_chat_from_env("CHAT_MODEL")
    assert chat is not None
    try:
        reply = chat.complete([{"role": "user", "content": "reply ok"}])
    except ModelQuotaExhausted:
        pytest.skip("chat model free quota is exhausted")
    except RuntimeError as exc:
        if "insufficient_quota" in str(exc):
            pytest.skip("chat model free quota is exhausted")
        raise
    assert reply.strip()


@pytest.mark.skipif(
    not _models_configured(), reason="chat and intent models are not configured"
)
def test_live_intent_model_classifies_beijing_tomorrow() -> None:
    chat = dashscope_chat_from_env("INTENT_MODEL")
    assert chat is not None
    try:
        guess = StructuredIntentModel(chat).classify("帮我查询明天北京天气")
    except ModelQuotaExhausted:
        pytest.skip("intent model free quota is exhausted")
    except RuntimeError as exc:
        if "insufficient_quota" in str(exc):
            pytest.skip("intent model free quota is exhausted")
        raise
    assert guess.intent == "weather"
    assert guess.confidence >= 0.7


@pytest.mark.skipif(
    not _models_configured(), reason="chat and intent models are not configured"
)
def test_live_intent_model_classifies_a_plain_weather_question() -> None:
    chat = dashscope_chat_from_env("INTENT_MODEL")
    assert chat is not None
    try:
        guess = StructuredIntentModel(chat).classify("明天广州的天气怎么样")
    except ModelQuotaExhausted:
        pytest.skip("intent model free quota is exhausted")
    except RuntimeError as exc:
        if "insufficient_quota" in str(exc):
            pytest.skip("intent model free quota is exhausted")
        raise
    assert guess.intent == "weather"
    assert guess.confidence >= 0.7
