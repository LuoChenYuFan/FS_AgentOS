"""规划行程 calls Amap for a route or a one-day city plan."""

import httpx
import pytest
from fs_agent_runtime.agents import AgentRunner
from fs_agent_runtime.itinerary import amap_api_key, plan_trip
from fs_agent_runtime.planner import RulePlanner
from fs_agent_runtime.trip_memory import BUSY_REPLY, NEED_PLACES_REPLY
from fs_agent_runtime.registry import load_catalog
from fs_common.contracts.intent import IntentLayer, IntentRoute
from fs_intent_funnel.funnel import IntentFunnel
from fs_intent_funnel.rules import rule_scores
from fs_orchestrator.chat_planner import ChatPlanner
from fs_orchestrator.schedule import CollaborationService

_SUBWAY = "告诉我从北京西站到北京南站我如果想做地铁，我该怎么换乘，大概用时多长时间？"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_transit_route_uses_the_returned_stops() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "secret-key" not in str(request.url)
        assert request.url.params["key"] == "test-key"
        path = request.url.path
        if path.endswith("/place/text"):
            name = request.url.params["keywords"]
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "pois": [
                        {
                            "name": name,
                            "location": "116.38,39.90",
                            "cityname": "北京市",
                            "address": name,
                            "type": "交通设施",
                        }
                    ],
                },
            )
        assert path.endswith("/transit/integrated")
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "transits": [
                        {
                            "duration": "1800",
                            "walking_distance": "400",
                            "cost": "5",
                            "segments": [
                                {
                                    "walking": {"distance": "200"},
                                    "bus": {
                                        "buslines": [
                                            {
                                                "name": "地铁14号线",
                                                "departure_stop": {"name": "北京南站"},
                                                "arrival_stop": {"name": "南锣鼓巷"},
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ]
                },
            },
        )

    with _client(handler) as client:
        result = plan_trip(
            {"起点": "北京南站", "终点": "故宫", "方式": "公交"},
            client=client,
            api_key="test-key",
            secret="",
        )
    assert result["itinerary"]["mode"] == "公交/地铁"
    assert result["itinerary"]["places"] == ["北京南站", "故宫"]
    assert result["cost_estimate"] == 5
    assert "地铁14号线" in result["summary"]
    assert "test-key" not in result["summary"]


def test_a_city_plan_lists_the_places() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place/text"):
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "pois": [
                        {
                            "name": "西湖",
                            "location": "120.14,30.24",
                            "cityname": "杭州市",
                            "address": "西湖风景名胜区",
                            "type": "风景名胜",
                        },
                        {
                            "name": "灵隐寺",
                            "location": "120.10,30.24",
                            "cityname": "杭州市",
                            "address": "灵隐路",
                            "type": "风景名胜",
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "5200",
                            "duration": "900",
                            "tolls": "0",
                            "steps": [{"instruction": "沿南山路行驶", "distance": "800"}],
                        }
                    ]
                },
            },
        )

    with _client(handler) as client:
        result = plan_trip(
            {"城市": "杭州", "关键词": "西湖", "天数": "1"},
            client=client,
            api_key="test-key",
            secret="",
        )
    assert result["itinerary"]["city"] == "杭州"
    assert result["itinerary"]["places"] == ["西湖", "灵隐寺"]
    assert "西湖" in result["summary"]
    assert "灵隐寺" in result["summary"]


def test_missing_ends_are_rejected_without_calling_amap() -> None:
    with pytest.raises(ValueError, match="缺少终点"):
        plan_trip({"起点": "北京南站"}, api_key="test-key")
    with pytest.raises(ValueError, match="AMAP_API_KEY"):
        plan_trip({"城市": "杭州"}, api_key="")


def test_agent_runner_calls_the_itinerary_skill() -> None:
    catalog = load_catalog()
    agent = catalog.agent("行程规划")
    assert agent is not None
    assert "规划行程" in agent.allowed_skills

    def itinerary(arguments: dict[str, object]) -> dict[str, object]:
        assert arguments["城市"] == "上海"
        return {
            "summary": "上海一日",
            "itinerary": {"city": "上海", "days": 1, "places": ["外滩"]},
            "cost_estimate": 0,
        }

    result = AgentRunner(itinerary=itinerary).execute(
        agent=agent,
        args={"skill": "规划行程", "城市": "上海"},
        skill="规划行程",
    )
    assert result["itinerary"]["places"] == ["外滩"]


def test_a_from_to_question_is_itinerary_not_the_knowledge_base() -> None:
    assert rule_scores(_SUBWAY) == {"itinerary": 0.95}
    assert rule_scores("张博洋的联系方式") == {"knowledge": 0.95}
    judged = IntentFunnel().decide(_SUBWAY)
    assert judged.decision.intent == "itinerary"
    assert judged.decision.route is IntentRoute.ORCHESTRATE
    assert judged.decision.layer_hit is IntentLayer.RULES

    dag = RulePlanner().plan(_SUBWAY, attempt=1, errors=[])
    assert [task.agent for task in dag.tasks] == ["行程规划"]
    assert dag.tasks[0].args["skill"] == "规划行程"
    assert dag.tasks[0].args["起点"] == "北京西站"
    assert dag.tasks[0].args["终点"] == "北京南站"
    assert dag.tasks[0].args["方式"] == "公交"

    class Boom:
        def complete(self, messages: list[dict[str, str]]) -> str:
            del messages
            raise AssertionError("from-to questions must not ask the planner model")

    planned = ChatPlanner(Boom()).plan(_SUBWAY, attempt=1, errors=[])
    assert planned.tasks[0].args["终点"] == "北京南站"

    def itinerary(arguments: dict[str, object]) -> dict[str, object]:
        assert arguments["起点"] == "北京西站"
        assert arguments["终点"] == "北京南站"
        assert arguments["方式"] == "公交"
        return {"summary": "方案1：约 30 分钟，乘坐地铁7号线、地铁4号线", "itinerary": {}, "cost_estimate": 5}

    outcome = CollaborationService(agents=AgentRunner(itinerary=itinerary)).execute_outcome(
        task_id="run_subway",
        payload={"text": _SUBWAY, "tenant_id": "tenant_a"},
    )
    assert outcome.status == "done"
    assert "30 分钟" in outcome.reply
    assert "地铁" in outcome.reply


_TAXI = "如果打车，要多长时间？多少钱？"


def test_a_taxi_followup_reuses_the_previous_trip() -> None:
    seen: list[dict[str, object]] = []

    def itinerary(arguments: dict[str, object]) -> dict[str, object]:
        seen.append(dict(arguments))
        return {
            "summary": f"{arguments.get('方式')}，约 25 分钟，打车约 48 元",
            "itinerary": {},
            "cost_estimate": 48,
        }

    service = CollaborationService(agents=AgentRunner(itinerary=itinerary))
    base = {"tenant_id": "tenant_follow", "user_id": "user_follow"}
    first = service.execute_outcome(task_id="run_follow_subway", payload={**base, "text": _SUBWAY})
    assert first.status == "done"
    assert seen[0]["方式"] == "公交"
    second = service.execute_outcome(task_id="run_follow_taxi", payload={**base, "text": _TAXI})
    assert second.status == "done"
    assert seen[1]["起点"] == "北京西站"
    assert seen[1]["终点"] == "北京南站"
    assert seen[1]["方式"] == "驾车"
    assert "48 元" in second.reply
    assert "task_id" not in second.reply
    missing = service.execute_outcome(
        task_id="run_follow_unknown",
        payload={"tenant_id": "tenant_new", "user_id": "user_new", "text": _TAXI},
    )
    assert missing.reply == NEED_PLACES_REPLY


def test_a_slow_itinerary_tells_the_user_to_retry() -> None:
    def itinerary(arguments: dict[str, object]) -> dict[str, object]:
        del arguments
        raise ValueError("skill 规划行程 timed out")

    outcome = CollaborationService(agents=AgentRunner(itinerary=itinerary)).execute_outcome(
        task_id="run_busy",
        payload={"text": _SUBWAY, "tenant_id": "tenant_busy", "user_id": "user_busy"},
    )
    assert outcome.status == "done"
    assert outcome.reply == BUSY_REPLY


def test_driving_summary_includes_the_taxi_fare() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/place/text"):
            name = request.url.params["keywords"]
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "pois": [
                        {
                            "name": name,
                            "location": "116.38,39.90",
                            "cityname": "北京市",
                            "address": name,
                            "type": "交通设施",
                        }
                    ],
                },
            )
        assert request.url.path.endswith("/direction/driving")
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "taxi_cost": "48",
                    "paths": [{"distance": "12000", "duration": "1500", "tolls": "0", "steps": []}],
                },
            },
        )

    with _client(handler) as client:
        result = plan_trip(
            {"起点": "北京西站", "终点": "北京南站", "方式": "驾车"},
            client=client,
            api_key="test-key",
            secret="",
        )
    assert result["cost_estimate"] == 48
    assert "25分钟" in result["summary"]
    assert "打车约 48 元" in result["summary"]


def test_live_amap_plans_beijing_transit_and_a_hangzhou_day() -> None:
    if not amap_api_key():
        pytest.skip("AMAP_API_KEY is not set")
    route = plan_trip({"起点": "北京南站", "终点": "故宫", "方式": "公交"})
    day = plan_trip({"城市": "杭州", "关键词": "西湖", "天数": "1"})
    assert route["itinerary"]["places"]
    assert "北京" in route["summary"] or "故宫" in route["summary"]
    assert amap_api_key() not in route["summary"]
    assert day["itinerary"]["places"]
    assert "西湖" in day["summary"] or any("西湖" in name for name in day["itinerary"]["places"])
    assert amap_api_key() not in day["summary"]
