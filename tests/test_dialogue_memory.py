"""Dialogue board: transcript, per-task slots, and follow-ups that survive a restart."""

from datetime import datetime, timedelta, timezone

from fs_agent_runtime.agents import AgentRunner
from fs_agent_runtime.trip_memory import NEED_WEATHER_CITY_REPLY, resolve_utterance
from fs_memory_sdk import MemoryScope
from fs_memory_service import DialogueBoard, MemoryService, PostgresMemoryStore
from fs_orchestrator.postgres import connect, migrate, postgres_configured
from fs_orchestrator.schedule import CollaborationService


def test_a_side_question_keeps_the_trip_and_a_later_taxi_reuses_it() -> None:
    seen: list[dict[str, object]] = []

    def itinerary(arguments: dict[str, object]) -> dict[str, object]:
        seen.append(dict(arguments))
        return {"summary": "已规划", "itinerary": {}, "cost_estimate": 0}

    service = CollaborationService(agents=AgentRunner(itinerary=itinerary))
    base = {"tenant_id": "tenant_board", "user_id": "user_board"}
    service.execute_outcome(
        task_id="trip",
        payload={**base, "text": "告诉我从北京西站到北京南站坐地铁怎么换乘，要多久"},
    )
    service.execute_outcome(task_id="sights", payload={**base, "text": "路上有什么好的景点"})
    service.execute_outcome(task_id="taxi", payload={**base, "text": "打车需要多长时间"})
    assert [item["方式"] for item in seen] == ["公交", "驾车"]
    assert seen[1]["起点"] == "北京西站"
    assert seen[1]["终点"] == "北京南站"
    state = service.dialogue.load("tenant_board", "user_board")
    assert state.slots["itinerary"]["mode"] == "驾车"
    assert any(turn["text"] == "路上有什么好的景点" for turn in state.turns)


def test_weather_mail_and_return_trip_use_their_own_slots() -> None:
    seen: list[dict[str, object]] = []

    def weather(arguments: dict[str, object]) -> dict[str, object]:
        seen.append(dict(arguments))
        return {"summary": "晴", "temp": 20, "condition": "晴"}

    def itinerary(arguments: dict[str, object]) -> dict[str, object]:
        seen.append(dict(arguments))
        return {"summary": "返程", "itinerary": {}, "cost_estimate": 0}

    service = CollaborationService(agents=AgentRunner(weather=weather, itinerary=itinerary))
    base = {"tenant_id": "tenant_slots", "user_id": "user_slots"}
    service.execute_outcome(task_id="weather", payload={**base, "text": "帮我查一下北京今天的天气"})
    later = service.execute_outcome(task_id="tomorrow", payload={**base, "text": "明天呢"})
    assert seen[0]["城市"] == "北京"
    assert seen[1]["城市"] == "北京"
    assert seen[1]["日期"] == "明天"
    assert "晴" in later.reply

    service.execute_outcome(
        task_id="ride",
        payload={**base, "text": "从北京西站到北京南站坐地铁怎么走"},
    )
    service.execute_outcome(task_id="back", payload={**base, "text": "打车回去要多久"})
    assert seen[-1]["起点"] == "北京南站"
    assert seen[-1]["终点"] == "北京西站"
    assert seen[-1]["方式"] == "驾车"
    slots = service.dialogue.load("tenant_slots", "user_slots").slots
    assert slots["weather"]["city"] == "北京"
    assert slots["itinerary"]["origin"] == "北京南站"


def test_slots_merge_and_an_idle_session_expires() -> None:
    start = datetime(2026, 10, 4, tzinfo=timezone.utc)
    current = {"at": start}
    board = DialogueBoard.in_memory(clock=lambda: current["at"])
    board.commit(
        "tenant_idle",
        "user_idle",
        user_text="从北京西站到北京南站",
        assistant_text="地铁约 30 分钟",
        slot_patch={"itinerary": {"origin": "北京西站", "destination": "北京南站", "mode": "公交"}},
    )
    board.commit(
        "tenant_idle",
        "user_idle",
        user_text="北京今天天气",
        assistant_text="晴",
        slot_patch={"weather": {"city": "北京", "date": "今天"}},
    )
    kept = board.load("tenant_idle", "user_idle")
    assert kept.slots["itinerary"]["origin"] == "北京西站"
    assert kept.slots["weather"]["city"] == "北京"
    assert resolve_utterance("如果打车，要多长时间？多少钱？", kept.slots) == (
        "从北京西站到北京南站如果打车，要多长时间？多少钱？"
    )
    assert resolve_utterance("帮我查询香港明天的天气", kept.slots) is None
    current["at"] = start + timedelta(hours=7)
    expired = board.load("tenant_idle", "user_idle")
    assert expired.turns == []
    assert expired.slots["itinerary"]["origin"] == ""


def test_two_conversations_do_not_share_trip_slots() -> None:
    service = CollaborationService(agents=AgentRunner(itinerary=_fixed_trip))
    west = {"tenant_id": "tenant_conv", "user_id": "user_conv", "conversation_id": "west"}
    east = {"tenant_id": "tenant_conv", "user_id": "user_conv", "conversation_id": "east"}
    service.execute_outcome(
        task_id="west",
        payload={**west, "text": "从北京西站到北京南站坐地铁怎么走"},
    )
    service.execute_outcome(
        task_id="east",
        payload={**east, "text": "从上海站到人民广场坐地铁怎么走"},
    )
    west_state = service.dialogue.load("tenant_conv", "user_conv", conversation_id="west")
    east_state = service.dialogue.load("tenant_conv", "user_conv", conversation_id="east")
    assert west_state.slots["itinerary"]["origin"] == "北京西站"
    assert east_state.slots["itinerary"]["origin"] == "上海站"
    taxi = service.execute_outcome(task_id="west_taxi", payload={**west, "text": "如果打车要多久"})
    assert "北京西站" in taxi.reply or taxi.status == "done"


def _fixed_trip(arguments: dict[str, object]) -> dict[str, object]:
    return {
        "summary": f"{arguments.get('起点')}到{arguments.get('终点')}，{arguments.get('方式')}",
        "itinerary": {},
        "cost_estimate": 0,
    }


def test_a_long_term_home_city_fills_a_weather_followup() -> None:
    board = DialogueBoard.in_memory()
    board.prefer("tenant_pref", "user_pref", "home_city", "北京")
    service = CollaborationService(agents=AgentRunner(weather=_fixed_weather), dialogue=board)
    later = service.execute_outcome(
        task_id="pref_weather",
        payload={"tenant_id": "tenant_pref", "user_id": "user_pref", "text": "明天呢"},
    )
    assert later.status == "done"
    assert "晴" in later.reply
    assert service.dialogue.load("tenant_pref", "user_pref").slots["weather"]["city"] == "北京"


def _fixed_weather(arguments: dict[str, object]) -> dict[str, object]:
    assert arguments["城市"] == "北京"
    return {"summary": "晴, 18℃", "temp": 18, "condition": "晴"}


def test_weather_without_a_city_asks_then_uses_the_answer() -> None:
    seen: list[dict[str, object]] = []

    def weather(arguments: dict[str, object]) -> dict[str, object]:
        seen.append(dict(arguments))
        return {"summary": f"{arguments['城市']}晴", "temp": 20, "condition": "晴"}

    service = CollaborationService(agents=AgentRunner(weather=weather))
    base = {"tenant_id": "tenant_ask_wx", "user_id": "user_ask_wx"}
    asked = service.execute_outcome(task_id="ask", payload={**base, "text": "明天天气怎么样"})
    assert asked.reply == NEED_WEATHER_CITY_REPLY
    assert seen == []
    answered = service.execute_outcome(task_id="city", payload={**base, "text": "香港"})
    assert seen[0]["城市"] == "香港"
    assert seen[0]["日期"] == "明天"
    assert "香港" in answered.reply


def test_weather_reuses_a_city_named_in_recent_chat() -> None:
    seen: list[dict[str, object]] = []

    def weather(arguments: dict[str, object]) -> dict[str, object]:
        seen.append(dict(arguments))
        return {"summary": f"{arguments['城市']}多云", "temp": 22, "condition": "多云"}

    service = CollaborationService(agents=AgentRunner(weather=weather))
    base = {"tenant_id": "tenant_ctx_wx", "user_id": "user_ctx_wx"}
    service.execute_outcome(task_id="chat", payload={**base, "text": "我想去三亚玩几天"})
    later = service.execute_outcome(task_id="wx", payload={**base, "text": "明天天气怎么样"})
    assert seen[0]["城市"] == "三亚"
    assert "三亚" in later.reply


def test_postgres_board_reloads_after_a_new_instance() -> None:
    if not postgres_configured():
        import pytest

        pytest.skip("PostgreSQL credentials are not configured")
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()
    tenant = "tenant_dialogue_pg"
    user = "user_dialogue_pg"
    first = DialogueBoard(MemoryService(PostgresMemoryStore(connect), cache=_NoCache()))
    first.commit(
        tenant,
        user,
        user_text="从北京西站到北京南站",
        assistant_text="约 30 分钟",
        slot_patch={"itinerary": {"origin": "北京西站", "destination": "北京南站", "mode": "公交"}},
    )
    second = DialogueBoard(MemoryService(PostgresMemoryStore(connect), cache=_NoCache()))
    loaded = second.load(tenant, user)
    assert loaded.slots["itinerary"]["destination"] == "北京南站"
    assert loaded.turns[0]["text"] == "从北京西站到北京南站"
    assert MemoryScope.WORKING.value == "working"


class _NoCache:
    def get(self, key: str) -> str | None:
        del key
        return None

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        del key, value, ttl_seconds

    def delete(self, key: str) -> None:
        del key
