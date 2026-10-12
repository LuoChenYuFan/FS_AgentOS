"""查天气 calls OpenWeather, and a daily forecast when the day is still inside 10 days."""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fs_agent_runtime.agents import AgentRunner
from fs_agent_runtime.registry import load_catalog
from fs_agent_runtime.weather import lookup_weather, openweather_api_key

_TIANJIN = [
    {"name": "Amatsu", "lat": 35.1, "lon": 140.1, "country": "JP"},
    {
        "name": "Tianjin",
        "lat": 39.1,
        "lon": 117.2,
        "country": "CN",
        "state": "Tianjin",
        "local_names": {"zh": "天津"},
    },
]


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_today_uses_current_weather_and_keeps_tianjin_in_china() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.params["appid"] == "test-key"
        if request.url.path.endswith("/direct"):
            assert request.url.params["q"] == "Tianjin,CN"
            return httpx.Response(200, json=_TIANJIN)
        assert request.url.path.endswith("/weather")
        assert request.url.params["units"] == "metric"
        assert request.url.params["lang"] == "zh_cn"
        assert request.url.params["lat"] == "39.1"
        return httpx.Response(
            200,
            json={
                "weather": [{"description": "晴"}],
                "main": {"temp": 26, "feels_like": 24, "humidity": 40},
                "wind": {"speed": 3.1},
            },
        )

    with _client(handler) as client:
        result = lookup_weather({"城市": "天津市", "日期": "今天"}, client=client, api_key="test-key")
    assert calls == ["/geo/1.0/direct", "/data/2.5/weather"]
    assert result["condition"] == "晴"
    assert result["temp"] == 26
    assert "今天实况" in result["summary"]
    assert "天津" in result["summary"]


def test_tomorrow_uses_the_midday_forecast() -> None:
    tz = timezone(timedelta(seconds=28800))
    target = datetime.now(tz).date() + timedelta(days=1)
    morning = datetime(target.year, target.month, target.day, 6, tzinfo=tz)
    noon = datetime(target.year, target.month, target.day, 12, tzinfo=tz)
    evening = datetime(target.year, target.month, target.day, 18, tzinfo=tz)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/direct"):
            assert request.url.params["q"] == "Beijing,CN"
            return httpx.Response(
                200,
                json=[{"name": "Beijing", "lat": 39.9, "lon": 116.4, "country": "CN", "local_names": {"zh": "北京"}}],
            )
        assert request.url.path.endswith("/forecast")
        return httpx.Response(
            200,
            json={
                "city": {"timezone": 28800},
                "list": [
                    {
                        "dt": int(morning.timestamp()),
                        "main": {"temp": 18},
                        "weather": [{"description": "小雨"}],
                        "wind": {"speed": 2},
                    },
                    {
                        "dt": int(noon.timestamp()),
                        "main": {"temp": 22},
                        "weather": [{"description": "多云"}],
                        "wind": {"speed": 4},
                    },
                    {
                        "dt": int(evening.timestamp()),
                        "main": {"temp": 20},
                        "weather": [{"description": "晴"}],
                        "wind": {"speed": 3},
                    },
                ],
            },
        )

    with _client(handler) as client:
        result = lookup_weather({"城市": "北京", "日期": "明天"}, client=client, api_key="test-key")
    assert result["condition"] == "多云"
    assert result["temp"] == 22
    assert "明天预报" in result["summary"]
    assert "最高气温：22.0°C" in result["summary"]
    assert "最低气温：18.0°C" in result["summary"]
    assert "中午气温：22°C" in result["summary"]


def test_a_missing_city_is_not_guessed() -> None:
    with pytest.raises(ValueError, match="未提供城市名"):
        lookup_weather({"日期": "今天"}, api_key="test-key")


def test_the_day_after_tomorrow_uses_that_days_forecast() -> None:
    tz = timezone(timedelta(seconds=28800))
    target = datetime.now(tz).date() + timedelta(days=2)
    noon = datetime(target.year, target.month, target.day, 12, tzinfo=tz)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/direct"):
            return httpx.Response(
                200,
                json=[{"name": "Beijing", "lat": 39.9, "lon": 116.4, "country": "CN", "local_names": {"zh": "北京"}}],
            )
        assert request.url.path.endswith("/forecast")
        return httpx.Response(
            200,
            json={
                "city": {"timezone": 28800},
                "list": [
                    {
                        "dt": int(noon.timestamp()),
                        "main": {"temp": 19},
                        "weather": [{"description": "小雨"}],
                        "wind": {"speed": 2.5},
                    }
                ],
            },
        )

    with _client(handler) as client:
        result = lookup_weather({"城市": "北京", "日期": "后天"}, client=client, api_key="test-key")
    assert result["condition"] == "小雨"
    assert result["temp"] == 19
    assert "后天预报" in result["summary"]


def test_a_day_inside_ten_days_uses_the_daily_forecast_when_hourly_slots_are_missing() -> None:
    start = date(2026, 10, 2)
    target = (start + timedelta(days=9)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/direct"):
            return httpx.Response(
                200,
                json=[{"name": "Beijing", "lat": 39.9, "lon": 116.4, "country": "CN", "local_names": {"zh": "北京"}}],
            )
        if request.url.host.endswith("open-meteo.com"):
            assert request.url.params["forecast_days"] == "10"
            return httpx.Response(
                200,
                json={
                    "daily": {
                        "time": [(start + timedelta(days=i)).isoformat() for i in range(10)],
                        "weather_code": [0] * 10,
                        "temperature_2m_max": [21] * 10,
                        "temperature_2m_min": [14] * 10,
                    },
                    "hourly": {
                        "time": [f"{target}T12:00"],
                        "temperature_2m": [18.5],
                        "weather_code": [1],
                        "wind_speed_10m": [3.2],
                    },
                },
            )
        return httpx.Response(200, json={"city": {"timezone": 28800}, "list": []})

    with _client(handler) as client:
        result = lookup_weather({"城市": "北京", "日期": "9天后"}, client=client, api_key="test-key")
    assert result["condition"] == "大部晴朗"
    assert result["temp"] == 18.5
    assert "最高气温：21°C" in result["summary"]


def test_a_date_beyond_ten_days_is_rejected_before_the_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("should not call a weather service")

    with _client(handler) as client, pytest.raises(ValueError, match="查询的时间比较长") as caught:
        lookup_weather({"城市": "北京", "日期": "10天后"}, client=client, api_key="test-key")
    assert "暂时只支持查询10天内的天气情况" in str(caught.value)


def test_a_past_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="已经过去了"):
        lookup_weather({"城市": "北京", "日期": "昨天"}, api_key="test-key")


def test_a_missing_key_stops_before_the_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("should not call OpenWeather")

    with _client(handler) as client, pytest.raises(ValueError, match="OPENWEATHER_API_KEY"):
        lookup_weather({"城市": "北京"}, client=client, api_key="")


def test_a_foreign_match_is_rejected_when_the_city_is_known() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[{"name": "Amatsu", "lat": 35.1, "lon": 140.1, "country": "JP"}])

    with _client(handler) as client, pytest.raises(ValueError, match="目标国家"):
        lookup_weather({"城市": "天津"}, client=client, api_key="test-key")


def test_an_http_error_does_not_include_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "test-key" in str(request.url)
        return httpx.Response(401, json={"cod": 401})

    with _client(handler) as client, pytest.raises(ValueError, match="HTTP 401") as caught:
        lookup_weather({"城市": "北京"}, client=client, api_key="test-key")
    assert "test-key" not in str(caught.value)


def test_agent_runner_returns_the_weather_fields() -> None:
    catalog = load_catalog()
    agent = catalog.agent("聊天助手")
    assert agent is not None

    def weather(arguments: dict[str, object]) -> dict[str, object]:
        assert arguments["城市"] == "上海"
        assert arguments["日期"] == "明天"
        return {"summary": "多云, 21℃", "temp": 21, "condition": "多云"}

    result = AgentRunner(weather=weather).execute(
        agent=agent,
        args={"skill": "查天气", "城市": "上海", "日期": "明天"},
        skill="查天气",
    )
    assert result == {"summary": "多云, 21℃", "temp": 21, "condition": "多云"}


def test_live_openweather_reports_beijing_today_and_tianjin_tomorrow() -> None:
    if not openweather_api_key():
        pytest.skip("OPENWEATHER_API_KEY is not set")
    today = lookup_weather({"城市": "北京", "日期": "今天"}, api_key=openweather_api_key())
    tomorrow = lookup_weather({"城市": "天津", "日期": "明天"}, api_key=openweather_api_key())
    later = lookup_weather({"城市": "北京", "日期": "后天"}, api_key=openweather_api_key())
    within_ten = lookup_weather({"城市": "北京", "日期": "9天后"}, api_key=openweather_api_key())
    assert today["condition"]
    assert isinstance(today["temp"], float)
    assert "今天实况" in today["summary"]
    assert "北京" in today["summary"]
    assert tomorrow["condition"]
    assert isinstance(tomorrow["temp"], float)
    assert "明天预报" in tomorrow["summary"]
    assert "天津" in tomorrow["summary"]
    assert later["condition"]
    assert "后天预报" in later["summary"]
    assert within_ten["condition"]
    assert isinstance(within_ten["temp"], float)


def test_amap_tomorrow_uses_the_forecast_cast(monkeypatch: pytest.MonkeyPatch) -> None:
    from fs_agent_runtime.skills.loader import skill_module

    weather = skill_module("查天气")
    monkeypatch.setattr(weather, "amap_weather_api_key", lambda: "amap-test")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.params["key"] == "amap-test"
        if request.url.path.endswith("/geocode/geo"):
            return httpx.Response(
                200,
                json={"status": "1", "geocodes": [{"adcode": "500000", "city": "重庆市"}]},
            )
        assert request.url.path.endswith("/weather/weatherInfo")
        assert request.url.params["city"] == "500000"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "forecasts": [
                    {
                        "city": "重庆市",
                        "casts": [
                            {"date": "2026-10-04", "dayweather": "晴", "daytemp": "28", "nighttemp": "18", "daywind": "北", "daypower": "2"},
                            {"date": "2026-10-05", "dayweather": "小雨", "daytemp": "24", "nighttemp": "16", "daywind": "北", "daypower": "1"},
                        ],
                    }
                ],
            },
        )

    with _client(handler) as client:
        result = weather.lookup_weather({"城市": "重庆", "日期": "明天"}, client=client)
    assert calls == ["/v3/geocode/geo", "/v3/weather/weatherInfo"]
    assert result["condition"] == "小雨"
    assert result["temp"] == 24
    assert "明天预报" in result["summary"]
    assert "重庆" in result["summary"]
    assert "amap-test" not in result["summary"]


def test_amap_unknown_region_falls_back_to_open_meteo(monkeypatch: pytest.MonkeyPatch) -> None:
    from fs_agent_runtime.skills.loader import skill_module

    weather = skill_module("查天气")
    monkeypatch.setattr(weather, "amap_weather_api_key", lambda: "amap-test")
    tomorrow = (datetime.now().astimezone().date() + timedelta(days=1)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "restapi.amap.com" and request.url.path.endswith("/weather/weatherInfo"):
            return httpx.Response(200, json={"status": "0", "info": "UNKNOWN_ERROR", "infocode": "20003"})
        if request.url.host == "geocoding-api.open-meteo.com":
            assert "Hong" in request.url.params["name"]
            return httpx.Response(
                200,
                json={"results": [{"name": "香港", "latitude": 22.3, "longitude": 114.17, "country": "中国"}]},
            )
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "daily": {
                        "time": [datetime.now().astimezone().date().isoformat(), tomorrow],
                        "weather_code": [1, 3],
                        "temperature_2m_max": [28, 27],
                        "temperature_2m_min": [24, 23],
                    },
                    "hourly": {
                        "time": [f"{tomorrow}T12:00"],
                        "temperature_2m": [26],
                        "weather_code": [3],
                        "wind_speed_10m": [3.2],
                    },
                },
            )
        raise AssertionError(request.url)

    with _client(handler) as client:
        result = weather.lookup_weather({"城市": "香港", "日期": "明天"}, client=client)
    assert "香港" in result["summary"]
    assert "明天预报" in result["summary"]
    assert result["temp"] == 26


def test_live_amap_reports_chongqing_tomorrow() -> None:
    from fs_agent_runtime.skills.loader import skill_module

    weather = skill_module("查天气")
    if not weather.amap_weather_api_key():
        pytest.skip("AMAP_TRAFFIC_API_KEY is not set")
    result = weather.lookup_weather({"城市": "重庆", "日期": "明天"})
    assert result["condition"]
    assert isinstance(result["temp"], (int, float))
    assert "明天预报" in result["summary"]
    assert "重庆" in result["summary"]
    assert weather.amap_weather_api_key() not in result["summary"]
