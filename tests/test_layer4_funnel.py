"""Layer 4: a fixed phrase stays on the regex step, and a vague sentence stops."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from fs_common import new_id
from fs_common.contracts.intent import (
    INTENT_UNKNOWN,
    IntentDecisionRecord,
    IntentLayer,
    IntentRoute,
)
from fs_gateway.context import GatewayState
from fs_gateway.main import _chat_reply, create_app
from fs_gateway.settings import GatewaySettings
from fs_intent_funnel import (
    FunnelConfig,
    IntentFunnel,
    IntentGuess,
    MemoryDecisionLog,
    MemoryIntentIndex,
    PostgresDecisionLog,
    promote_repeated,
)
from fs_intent_funnel.vectors import IntentHit
from fs_orchestrator import ThinOrchestrator
from fs_orchestrator.postgres import connect, migrate, postgres_configured


class SpyOrchestrator(ThinOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def run(
        self, *, task_id: str | None = None, input_payload: dict | None = None
    ) -> dict[str, str]:
        self.calls += 1
        return await super().run(task_id=task_id, input_payload=input_payload or {})


class SpyModel:
    def __init__(self, guess: IntentGuess) -> None:
        self.calls = 0
        self.guess = guess

    def classify(self, text: str) -> IntentGuess:
        del text
        self.calls += 1
        return self.guess


class SpyKnowledge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def accept(self, *, tenant_id: str, text: str, intent: str) -> dict[str, str]:
        del tenant_id
        self.calls.append(text)
        return {"status": "accepted", "route": "knowledge", "intent": intent}


class ScriptIndex:
    def __init__(self, hits: list[IntentHit]) -> None:
        self.hits = hits

    def search(self, text: str, *, limit: int = 2) -> list[IntentHit]:
        del text
        return self.hits[:limit]


def _low_models() -> tuple[SpyModel, SpyModel]:
    guess = IntentGuess(intent="chat", confidence=0.4)
    return SpyModel(guess), SpyModel(guess)


def _funnel(
    *,
    index: MemoryIntentIndex | ScriptIndex | None = None,
    config: FunnelConfig | None = None,
    small: SpyModel | None = None,
    large: SpyModel | None = None,
) -> tuple[IntentFunnel, SpyModel, SpyModel]:
    small_model, large_model = _low_models()
    funnel = IntentFunnel(
        config=config,
        index=index if index is not None else MemoryIntentIndex([]),
        small_model=small or small_model,
        large_model=large or large_model,
    )
    return funnel, funnel.small_model, funnel.large_model  # type: ignore[return-value]


def _client(
    funnel: IntentFunnel,
    knowledge: SpyKnowledge,
    decisions: MemoryDecisionLog | PostgresDecisionLog,
) -> tuple[TestClient, SpyOrchestrator]:
    settings = GatewaySettings(
        jwt_secret="test-secret-must-be-at-least-32-bytes",
        password_iterations=1000,
        cors_origins="http://localhost:8000",
    )
    state = GatewayState.create(settings)
    state.users.add(
        username="ada",
        password="secret",
        user_id="user_ada",
        tenant_id="tenant_a",
        scopes=["tasks:write", "kb:read:hr"],
    )
    spy = SpyOrchestrator()
    client = TestClient(create_app(settings, state, spy, funnel, decisions, knowledge))
    login = client.post(
        "/v1/auth/login", json={"username": "ada", "password": "secret"}
    )
    assert login.status_code == 200
    return client, spy


def _auth(client: TestClient) -> dict[str, str]:
    token = client.post(
        "/v1/auth/login", json={"username": "ada", "password": "secret"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_policy_id_stops_at_regex_and_does_not_call_a_model() -> None:
    funnel, small, large = _funnel()
    knowledge = SpyKnowledge()
    decisions = MemoryDecisionLog()
    client, spy = _client(funnel, knowledge, decisions)
    response = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=_auth(client)
    )
    body = response.json()
    assert response.status_code == 200
    assert body["route"] == "knowledge"
    assert body["intent"] == "policy_lookup"
    assert small.calls == 0
    assert large.calls == 0
    assert spy.calls == 0
    assert knowledge.calls == ["查阅制度编号 HR-2024-001"]
    assert decisions.rows[0].layer_hit is IntentLayer.REGEX
    assert decisions.rows[0].confidence == 1
    assert decisions.rows[0].failure_reason == ""


def test_vague_text_is_unknown_and_reaches_neither_rag_nor_an_agent() -> None:
    funnel, small, large = _funnel()
    knowledge = SpyKnowledge()
    client, spy = _client(funnel, knowledge, MemoryDecisionLog())
    headers = _auth(client)
    first = client.post("/v1/tasks", json={"text": "今天怎么样呀"}, headers=headers)
    second = client.post(
        "/v1/tasks",
        json={"text": "今天怎么样呀"},
        headers={**headers, "Idempotency-Key": first.headers["Idempotency-Key"]},
    )
    assert first.status_code == 200
    assert first.json()["route"] == "unknown"
    assert first.json()["intent"] == INTENT_UNKNOWN
    assert "还不够明确" in first.json()["message"]
    assert "run_id" not in first.json()
    assert second.json()["decision_id"] == first.json()["decision_id"]
    assert spy.calls == 0
    assert knowledge.calls == []
    assert small.calls == 1
    assert large.calls == 1


def test_a_clear_task_still_reaches_the_orchestrator_without_a_model() -> None:
    funnel, small, large = _funnel()
    knowledge = SpyKnowledge()
    client, spy = _client(funnel, knowledge, MemoryDecisionLog())
    response = client.post(
        "/v1/tasks", json={"text": "帮我发一封邮件"}, headers=_auth(client)
    )
    assert response.status_code == 200
    assert response.json()["route"] == "orchestrate"
    assert response.json()["run_id"]
    assert spy.calls == 1
    assert knowledge.calls == []
    assert small.calls == 0
    assert large.calls == 0


def test_a_policy_id_inside_a_longer_request_is_not_a_regex_hit() -> None:
    funnel, _, _ = _funnel()
    result = funnel.decide("查阅制度编号 HR-2024-001，再发一封邮件")
    assert result.decision.route is IntentRoute.ORCHESTRATE
    assert result.decision.layer_hit is IntentLayer.RULES
    assert result.attempts[0].layer is IntentLayer.REGEX
    assert result.attempts[0].passed is False


def test_vector_needs_a_gap_and_a_clear_example_skips_the_model() -> None:
    small, large = _low_models()
    crowded = IntentFunnel(
        index=ScriptIndex([IntentHit("knowledge", 0.9), IntentHit("send_email", 0.86)]),
        small_model=small,
        large_model=large,
    )
    vague = crowded.decide("年假还剩几天")
    assert vague.decision.route is IntentRoute.UNKNOWN
    assert small.calls == 1

    small.calls = 0
    large.calls = 0
    clear = IntentFunnel(
        index=ScriptIndex([IntentHit("schedule", 0.95), IntentHit("knowledge", 0.2)]),
        small_model=small,
        large_model=large,
    )
    decided = clear.decide("帮我订周五下午的会议室")
    assert decided.decision.route is IntentRoute.ORCHESTRATE
    assert decided.decision.layer_hit is IntentLayer.VECTOR
    assert small.calls == 0
    assert large.calls == 0


def test_small_model_can_be_skipped_and_a_second_intent_goes_to_the_planner() -> None:
    small = SpyModel(IntentGuess(intent="knowledge", confidence=0.99))
    large = SpyModel(
        IntentGuess(intent="knowledge", confidence=0.95, second_intent="send_email")
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        small_model=small,
        large_model=large,
    )
    result = funnel.decide("这句规则和样例都接不住")
    assert small.calls == 0
    assert large.calls == 1
    assert result.decision.route is IntentRoute.ORCHESTRATE
    assert result.decision.layer_hit is IntentLayer.LARGE_LLM


def test_repeated_large_model_phrases_are_promoted_offline() -> None:
    log = MemoryDecisionLog()
    index = MemoryIntentIndex([])
    for _ in range(3):
        log.write(_decision("年假怎么查", "knowledge", IntentLayer.LARGE_LLM))
    log.write(
        _decision("年假怎么查", "knowledge", IntentLayer.LARGE_LLM, false_positive=True)
    )
    log.write(_decision("只出现一次", "knowledge", IntentLayer.LARGE_LLM))
    added = promote_repeated(log, index, min_repeats=3)
    assert added == ["年假怎么查"]
    follow = IntentFunnel(
        index=index,
        small_model=SpyModel(IntentGuess(intent="chat", confidence=0.1)),
        large_model=SpyModel(IntentGuess(intent="chat", confidence=0.1)),
    )
    assert follow.decide("年假怎么查").decision.layer_hit is IntentLayer.VECTOR


def _decision(
    text: str,
    intent: str,
    layer: IntentLayer,
    *,
    false_positive: bool | None = None,
) -> IntentDecisionRecord:
    return IntentDecisionRecord(
        id=new_id("intent"),
        tenant_id="tenant_a",
        raw_text=text,
        intent=intent,
        route=IntentRoute.ORCHESTRATE,
        confidence=0.9,
        layer_hit=layer,
        passed=True,
        false_positive=false_positive,
    )


@pytest.mark.skipif(
    not postgres_configured(), reason="PostgreSQL credentials are not configured"
)
def test_decision_row_is_stored_for_the_winning_layer() -> None:
    funnel, _, _ = _funnel()
    result = funnel.decide("查阅制度编号 HR-2024-001")
    record = IntentDecisionRecord(
        id=f"intent_{uuid.uuid4().hex}",
        tenant_id=f"tenant_{uuid.uuid4().hex[:8]}",
        raw_text="查阅制度编号 HR-2024-001",
        latency_ms=result.latency_ms,
        **result.decision.model_dump(),
    )
    PostgresDecisionLog().write(record)
    conn = connect()
    try:
        migrate(conn)
        row = conn.execute(
            """
            SELECT route, layer_hit, passed, confidence, failure_reason, attempt_reasons
            FROM intent_decisions WHERE id = %s
            """,
            (record.id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    assert row["route"] == "knowledge"
    assert row["layer_hit"] == "regex"
    assert row["passed"] is True
    assert row["confidence"] == 1
    assert row["failure_reason"] == ""
    assert row["attempt_reasons"] == []


def test_a_broken_model_reply_falls_back_to_the_keyword_hit() -> None:
    large = SpyModel(
        IntentGuess(intent="unknown", confidence=0.0, failure_reason="parse_error")
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=large,
    )
    result = funnel.decide("帮我发一封邮件，查天气")
    assert large.calls == 1
    assert result.decision.route is IntentRoute.ORCHESTRATE
    assert result.decision.layer_hit is IntentLayer.RULES
    assert result.decision.intent == "send_email"
    assert result.decision.failure_reason == ""
    assert result.attempts[-1].failure_reason == "parse_error"


def test_a_broken_model_reply_falls_back_to_the_vector_hit() -> None:
    large = SpyModel(
        IntentGuess(intent="unknown", confidence=0.0, failure_reason="schema_error")
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=ScriptIndex([IntentHit("weather", 0.5)]),
        large_model=large,
    )
    result = funnel.decide("明天会下雨吗")
    assert result.decision.route is IntentRoute.ORCHESTRATE
    assert result.decision.layer_hit is IntentLayer.VECTOR
    assert result.decision.intent == "weather"


def test_a_broken_model_reply_without_an_earlier_hit_is_not_blamed_on_the_user() -> (
    None
):
    large = SpyModel(
        IntentGuess(intent="unknown", confidence=0.0, failure_reason="parse_error")
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=large,
    )
    decisions = MemoryDecisionLog()
    client, spy = _client(funnel, SpyKnowledge(), decisions)
    response = client.post(
        "/v1/tasks", json={"text": "今天怎么样呀"}, headers=_auth(client)
    )
    body = response.json()
    assert body["route"] == "unknown"
    assert "系统没有识别成" in body["message"]
    assert "还不够明确" not in body["message"]
    assert spy.calls == 0
    assert decisions.rows[0].failure_reason == "parse_error"
    assert decisions.rows[0].layer_hit is IntentLayer.NONE
    assert decisions.rows[0].raw_text == "今天怎么样呀"
    assert "parse_error" not in decisions.rows[0].raw_text
    assert {"layer": "large_llm", "failure_reason": "parse_error"} in decisions.rows[
        0
    ].attempt_reasons


def test_low_confidence_is_stored_apart_from_a_parse_error() -> None:
    large = SpyModel(
        IntentGuess(
            intent="weather", confidence=0.4, failure_reason="business_confidence"
        )
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=large,
    )
    decisions = MemoryDecisionLog()
    client, _spy = _client(funnel, SpyKnowledge(), decisions)
    response = client.post(
        "/v1/tasks", json={"text": "帮我查询明天北京天气"}, headers=_auth(client)
    )
    assert "还不够明确" in response.json()["message"]
    assert decisions.rows[0].failure_reason == "business_confidence"
    assert decisions.rows[0].confidence == 0.0
    assert (
        funnel.decide("帮我查询明天北京天气").attempts[-1].failure_reason
        == "business_confidence"
    )


def test_low_confidence_does_not_fall_back_to_a_weak_keyword() -> None:
    large = SpyModel(
        IntentGuess(
            intent="weather", confidence=0.4, failure_reason="business_confidence"
        )
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=large,
    )
    result = funnel.decide("帮我发一封邮件，查天气")
    assert result.decision.route is IntentRoute.UNKNOWN
    client, _spy = _client(funnel, SpyKnowledge(), MemoryDecisionLog())
    response = client.post(
        "/v1/tasks", json={"text": "帮我查询明天北京天气"}, headers=_auth(client)
    )
    assert "还不够明确" in response.json()["message"]


def test_a_down_model_falls_back_to_the_keyword_hit() -> None:
    large = SpyModel(
        IntentGuess(intent="unknown", confidence=0.0, failure_reason="model_error")
    )
    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=large,
    )
    result = funnel.decide("帮我发一封邮件，查天气")
    assert result.decision.route is IntentRoute.ORCHESTRATE
    assert result.decision.layer_hit is IntentLayer.RULES
    assert result.decision.intent == "send_email"
    assert result.decision.failure_reason == ""


def test_a_down_model_without_an_earlier_hit_is_not_called_unclear() -> None:
    class DownModel:
        def classify(self, text: str) -> IntentGuess:
            del text
            raise RuntimeError("timeout")

    funnel = IntentFunnel(
        config=FunnelConfig(skip_small_llm=True),
        index=MemoryIntentIndex([]),
        large_model=DownModel(),
    )
    decisions = MemoryDecisionLog()
    client, _spy = _client(funnel, SpyKnowledge(), decisions)
    response = client.post(
        "/v1/tasks", json={"text": "今天怎么样呀"}, headers=_auth(client)
    )
    assert "系统没有识别成" in response.json()["message"]
    assert "还不够明确" not in response.json()["message"]
    assert decisions.rows[0].failure_reason == "model_error"
    assert {"layer": "large_llm", "failure_reason": "model_error"} in decisions.rows[
        0
    ].attempt_reasons


def test_asking_which_skills_is_chat_and_does_not_open_the_library() -> None:
    from fs_gateway.platform.store import assistant_persona

    result = IntentFunnel().decide("都有哪些技能")
    assert result.decision.intent == "chat"
    assert result.decision.route is IntentRoute.ORCHESTRATE
    assert result.decision.layer_hit is IntentLayer.RULES

    class Chat:
        def complete(self, messages: list[dict[str, str]]) -> str:
            assert messages[1]["content"] == "都有哪些技能"
            return "可以查天气、查路线、查简历信息。"

    persona = assistant_persona(None)
    reply = asyncio.run(_chat_reply(Chat(), "都有哪些技能", persona=persona))
    assert reply == "可以查天气、查路线、查简历信息。"
    assert "查天气" in asyncio.run(_chat_reply(None, "都有哪些技能", persona=persona))
    assert "查酒店" not in str(persona["fallback"])
    assert "查简历信息" in str(persona["fallback"])
    assert (
        asyncio.run(_chat_reply(Chat(), "你是谁？", persona=persona))
        == "我是主人的AI小助理"
    )


def test_questions_about_zhang_open_the_library_and_mail_to_him_does_not() -> None:
    asked = IntentFunnel().decide("张博洋的电话是多少")
    assert asked.decision.intent == "knowledge"
    assert asked.decision.route is IntentRoute.KNOWLEDGE
    assert asked.decision.layer_hit is IntentLayer.RULES

    mailed = IntentFunnel().decide("给张博洋发邮件")
    assert mailed.decision.intent == "send_email"
    assert mailed.decision.route is IntentRoute.ORCHESTRATE
    assert mailed.decision.layer_hit is IntentLayer.RULES
