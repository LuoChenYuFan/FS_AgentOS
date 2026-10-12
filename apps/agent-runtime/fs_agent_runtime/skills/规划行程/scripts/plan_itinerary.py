"""规划行程的执行脚本。用高德 Web 服务查路线或城市行程。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, NoReturn

import httpx

GEO_URL = "https://restapi.amap.com/v3/geocode/geo"
PLACE_URL = "https://restapi.amap.com/v3/place/text"
DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"
WALKING_URL = "https://restapi.amap.com/v3/direction/walking"
TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
BICYCLING_URL = "https://restapi.amap.com/v4/direction/bicycling"
_HTTP_TIMEOUT = 8.0
_BUSY_REPLY = "当前远端服务繁忙，请稍后再试。"
_KEY_PARAM = re.compile(r"(key=)[^&\s\"']+")
_COORD = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")
_SPLIT = re.compile(r"[,，;；|]+")
_DAYS = re.compile(r"(\d+)")
_POI_NAME = re.compile(r"(站|机场|公园|广场|故宫|博物馆|大学|医院|景区|码头|政府|大厦)")

_MODE_LABELS = {"driving": "驾车", "walking": "步行", "transit": "公交/地铁", "bicycling": "骑行"}
_MODES = {
    "driving": "driving",
    "drive": "driving",
    "car": "driving",
    "驾车": "driving",
    "开车": "driving",
    "自驾": "driving",
    "打车": "driving",
    "乘车": "driving",
    "walking": "walking",
    "walk": "walking",
    "步行": "walking",
    "走路": "walking",
    "徒步": "walking",
    "transit": "transit",
    "bus": "transit",
    "subway": "transit",
    "metro": "transit",
    "公交": "transit",
    "地铁": "transit",
    "公共交通": "transit",
    "换乘": "transit",
    "bicycling": "bicycling",
    "cycling": "bicycling",
    "bike": "bicycling",
    "bicycle": "bicycling",
    "骑行": "bicycling",
    "自行车": "bicycling",
    "骑车": "bicycling",
}
_CITIES = (
    "乌鲁木齐",
    "呼和浩特",
    "石家庄",
    "哈尔滨",
    "北京",
    "上海",
    "天津",
    "重庆",
    "广州",
    "深圳",
    "杭州",
    "成都",
    "武汉",
    "西安",
    "南京",
    "苏州",
    "长沙",
    "郑州",
    "青岛",
    "大连",
    "宁波",
    "厦门",
    "沈阳",
    "长春",
    "济南",
    "太原",
    "合肥",
    "福州",
    "南昌",
    "南宁",
    "昆明",
    "贵阳",
    "海口",
    "三亚",
    "兰州",
    "西宁",
    "银川",
    "拉萨",
    "香港",
    "澳门",
    "台北",
)


def load_local_env() -> None:
    """Fill unset variables from the project .env. Never logs values."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        env_file = parent / ".env"
        if env_file.is_file() and (parent / "pyproject.toml").is_file():
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


def amap_api_key() -> str:
    load_local_env()
    return os.environ.get("AMAP_API_KEY", "").strip()


def amap_secret() -> str:
    load_local_env()
    return os.environ.get("AMAP_SECRET", "").strip()


def run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """技能入口。路线用起点、终点；城市行程用城市，可选关键词和天数。"""
    return plan_trip(arguments)


def plan_trip(
    arguments: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
    api_key: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    arguments = arguments or {}
    origin_raw = _text(arguments, "起点", "origin", "from", "start")
    destination_raw = _text(arguments, "终点", "destination", "dest", "end")
    city = _text(arguments, "城市", "city", "location")
    keywords = _text(arguments, "关键词", "keywords", "theme", "query")
    waypoints_raw = _text(arguments, "途经", "waypoints", "via")
    mode_raw = _text(arguments, "方式", "mode", "travel_mode")
    days = _days(_text(arguments, "天数", "days", "day"))
    mode = _mode(mode_raw)
    key = amap_api_key() if api_key is None else api_key.strip()
    sign_secret = amap_secret() if secret is None else secret.strip()
    if not key:
        _fail("MISSING_API_KEY", "未配置 AMAP_API_KEY。请在 .env 中填入高德开放平台 Web 服务 Key。")
    if mode_raw and not mode:
        _fail("INVALID_MODE", f"不支持的出行方式「{mode_raw}」。请使用驾车、步行、公交或骑行。")
    has_route = bool(origin_raw and destination_raw)
    if not has_route and not (city or keywords):
        if origin_raw:
            _fail("MISSING_DESTINATION", "缺少终点。请询问用户要去哪里，不要猜测。")
        if destination_raw:
            _fail("MISSING_ORIGIN", "缺少起点。请询问用户从哪里出发，不要猜测。")
        _fail("MISSING_ORIGIN", "缺少起点和终点，或要规划的城市。请先问清楚，不要猜测。")

    owns_client = client is None
    http = client or httpx.Client(timeout=_HTTP_TIMEOUT)
    try:
        if has_route:
            origin = _resolve_place(http, origin_raw, key, sign_secret, _city_in(origin_raw) or city)
            destination = _resolve_place(
                http,
                destination_raw,
                key,
                sign_secret,
                _city_in(destination_raw) or origin.get("city") or city,
            )
            stops = [
                _resolve_place(http, item, key, sign_secret, _city_in(item) or city)
                for item in _waypoints(waypoints_raw)
            ]
            routed = _plan_route(http, origin, destination, mode, key, sign_secret, stops or None)
            summary, cost = _format_route(routed, origin, destination, mode, stops or None)
            places = [origin["name"], *[item["name"] for item in stops], destination["name"]]
            return _result(summary, origin.get("city") or city, 1, mode, places, cost)
        summary, places, cost = _plan_day(
            http, city=city, keywords=keywords, days=days, mode=mode, api_key=key, secret=sign_secret
        )
        return _result(summary, city, days, mode, places, cost)
    except httpx.TimeoutException:
        _fail("BUSY", _BUSY_REPLY, key)
    except httpx.HTTPStatusError as exc:
        _fail("HTTP_STATUS", f"高德接口返回 HTTP {exc.response.status_code}。", key)
    except httpx.HTTPError as exc:
        _fail("NETWORK", f"网络错误（{_scrub(str(exc), key)}）。", key)
    finally:
        if owns_client:
            http.close()


def _result(
    summary: str, city: str, days: int, mode: str, places: list[str], cost: float
) -> dict[str, Any]:
    return {
        "summary": summary,
        "itinerary": {
            "city": city,
            "days": days,
            "mode": _MODE_LABELS.get(mode, mode),
            "places": places,
        },
        "cost_estimate": cost,
    }


def _text(arguments: dict[str, Any], *names: str) -> str:
    for name in names:
        value = arguments.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _mode(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "driving"
    return _MODES.get(text) or _MODES.get(text.casefold(), "")


def _days(raw: str) -> int:
    text = (raw or "").strip()
    if not text:
        return 1
    matched = _DAYS.search(text)
    if matched:
        return max(1, min(int(matched.group(1)), 3))
    if "两" in text or "二" in text:
        return 2
    if "三" in text:
        return 3
    return 1


def _waypoints(raw: str) -> list[str]:
    return [item.strip() for item in _SPLIT.split(raw or "") if item.strip()][:16]


def _city_in(place: str) -> str:
    compact = (place or "").replace(" ", "")
    for name in _CITIES:
        if name in compact:
            return name
    return ""


def _sign(params: dict[str, str], secret: str) -> str:
    pairs = sorted((key, value) for key, value in params.items() if key != "sig" and value)
    raw = "&".join(f"{key}={value}" for key, value in pairs) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _get(client: httpx.Client, url: str, params: dict[str, str], api_key: str, secret: str) -> dict[str, Any]:
    query = {key: value for key, value in params.items() if value}
    query["key"] = api_key
    query["output"] = "JSON"
    if secret:
        query["sig"] = _sign(query, secret)
    response = client.get(url, params=query)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        _fail("HTTP_STATUS", "高德返回的不是 JSON。", api_key)
    if not isinstance(data, dict):
        _fail("HTTP_STATUS", "高德返回的数据格式无法识别。", api_key)
    _raise_if_failed(data, api_key)
    return data


def _raise_if_failed(data: dict[str, Any], api_key: str) -> None:
    if str(data.get("errcode", "0")) not in {"0", "None"} and "paths" not in data and "route" not in data:
        if data.get("errcode") not in (None, 0, "0"):
            info = str(data.get("errmsg") or data.get("errdetail") or "高德接口调用失败")
            _fail("AMAP_ERROR", info, api_key)
    status = str(data.get("status", "1")).strip()
    if status in {"", "1"}:
        return
    info = str(data.get("info") or data.get("errmsg") or "高德接口调用失败")
    code = str(data.get("infocode") or data.get("errcode") or "")
    extra = f"（{code}）" if code else ""
    if code in {"10001", "10004"} or "USERKEY" in info.upper() or "INVALID_USER_KEY" in info:
        _fail("AMAP_ERROR", f"高德 Key 无效或类型不匹配{extra}。请在 .env 填入 Web 服务类型的 AMAP_API_KEY。", api_key)
    if code == "10003" or "OVER_LIMIT" in info:
        _fail("AMAP_ERROR", f"高德接口今日调用量已超限{extra}。", api_key)
    _fail("AMAP_ERROR", f"{info}{extra}", api_key)


def _looks_like_coord(value: str) -> bool:
    return bool(_COORD.match(value or ""))


def _search_pois(
    client: httpx.Client, keywords: str, city: str, api_key: str, secret: str, *, limit: int
) -> list[dict[str, str]]:
    data = _get(
        client,
        PLACE_URL,
        {
            "keywords": keywords or "风景名胜",
            "city": city,
            "citylimit": "true" if city else "false",
            "offset": str(max(1, min(limit, 20))),
            "page": "1",
            "extensions": "base",
        },
        api_key,
        secret,
    )
    found: list[dict[str, str]] = []
    for item in data.get("pois") or []:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "").strip()
        name = str(item.get("name") or "").strip()
        if not location or not name:
            continue
        found.append(
            {
                "name": name,
                "location": location,
                "city": str(item.get("cityname") or city).strip(),
                "address": str(item.get("address") or name).strip(),
                "type": str(item.get("type") or "").strip(),
            }
        )
        if len(found) >= limit:
            break
    return found


def _geocode(
    client: httpx.Client, address: str, api_key: str, secret: str, city_hint: str
) -> dict[str, str]:
    text = address.strip()
    if _looks_like_coord(text):
        lng, lat = (part.strip() for part in text.split(",", 1))
        location = f"{float(lng):.6f},{float(lat):.6f}"
        return {"name": location, "location": location, "city": city_hint, "address": location}
    data = _get(client, GEO_URL, {"address": text, "city": city_hint}, api_key, secret)
    geocodes = data.get("geocodes") or []
    if not geocodes:
        pois = _search_pois(client, text, city_hint, api_key, secret, limit=1)
        if pois:
            return pois[0]
        _fail("PLACE_NOT_FOUND", f"找不到地点「{text}」。请确认地名，不要用同一地名重试。", api_key)
    item = geocodes[0]
    location = str(item.get("location") or "").strip()
    if not location:
        _fail("PLACE_NOT_FOUND", f"地点「{text}」没有坐标。", api_key)
    city = str(item.get("city") or item.get("province") or city_hint).strip()
    if isinstance(item.get("city"), list):
        city = city_hint
    formatted = str(item.get("formatted_address") or text).strip()
    return {"name": formatted or text, "location": location, "city": city, "address": formatted or text}


def _prefer_poi(name: str) -> bool:
    text = (name or "").strip()
    if not text or _looks_like_coord(text) or re.search(r"\d+号", text):
        return False
    return bool(_POI_NAME.search(text)) or len(text) <= 8


def _resolve_place(
    client: httpx.Client, name: str, api_key: str, secret: str, city_hint: str
) -> dict[str, str]:
    if _prefer_poi(name):
        pois = _search_pois(client, name, city_hint, api_key, secret, limit=1)
        if pois:
            return pois[0]
    return _geocode(client, name, api_key, secret, city_hint)


def _direction_url(mode: str) -> str:
    return {"walking": WALKING_URL, "transit": TRANSIT_URL, "bicycling": BICYCLING_URL}.get(mode, DRIVING_URL)


def _plan_route(
    client: httpx.Client,
    origin: dict[str, str],
    destination: dict[str, str],
    mode: str,
    api_key: str,
    secret: str,
    waypoints: list[dict[str, str]] | None,
) -> dict[str, Any]:
    params = {"origin": origin["location"], "destination": destination["location"]}
    if mode == "transit":
        params["city"] = origin.get("city") or destination.get("city") or ""
        dest_city = destination.get("city") or ""
        if dest_city and dest_city != params["city"]:
            params["cityd"] = dest_city
        params["strategy"] = "3"
        params["nightflag"] = "0"
    elif mode == "driving":
        if waypoints:
            params["waypoints"] = ";".join(item["location"] for item in waypoints)
        params["extensions"] = "base"
        params["strategy"] = "0"
    data = _get(client, _direction_url(mode), params, api_key, secret)
    if mode == "bicycling" and data.get("errcode") not in (None, 0, "0"):
        _fail("AMAP_ERROR", str(data.get("errmsg") or data.get("errdetail") or "骑行路径规划失败。"), api_key)
    return data


def _distance(meters: object) -> str:
    try:
        value = float(meters)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    if value >= 1000:
        return f"{value / 1000:.1f}公里"
    return f"{int(value)}米"


def _duration(seconds: object) -> str:
    try:
        total = int(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    minutes = total // 60
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    return f"{max(minutes, 0)}分钟"


def _money(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _steps(steps: list[Any], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for index, step in enumerate(steps[:limit], start=1):
        if not isinstance(step, dict):
            continue
        instruction = str(step.get("instruction") or step.get("road") or "").strip()
        if instruction:
            lines.append(f"{index}. {instruction}（{_distance(step.get('distance'))}）")
    if len(steps) > limit:
        lines.append(f"……其余 {len(steps) - limit} 步已省略")
    return lines


def _format_route(
    data: dict[str, Any],
    origin: dict[str, str],
    destination: dict[str, str],
    mode: str,
    waypoints: list[dict[str, str]] | None,
) -> tuple[str, float]:
    if mode == "transit":
        return _format_transit(data, origin, destination)
    if mode == "bicycling":
        return _format_bike(data, origin, destination)
    return _format_path(data, origin, destination, mode, waypoints)


def _format_transit(data: dict[str, Any], origin: dict[str, str], destination: dict[str, str]) -> tuple[str, float]:
    transits = ((data.get("route") or {}).get("transits") or []) if isinstance(data.get("route"), dict) else []
    if not transits:
        _fail("AMAP_ERROR", "没有可用的公交或地铁方案。")
    lines = [f"起点：{origin['name']}", f"终点：{destination['name']}", "出行方式：公交/地铁"]
    cost = 0.0
    for index, transit in enumerate(transits[:2], start=1):
        if not isinstance(transit, dict):
            continue
        if index == 1:
            cost = _money(transit.get("cost"))
        lines.append(
            f"方案{index}：约 {_duration(transit.get('duration'))}，"
            f"步行 {_distance(transit.get('walking_distance'))}，票价约 {transit.get('cost') or '—'} 元"
        )
        step_no = 1
        for segment in transit.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            walking = segment.get("walking") if isinstance(segment.get("walking"), dict) else {}
            if _money(walking.get("distance")) > 0:
                lines.append(f"{step_no}. 步行 {_distance(walking.get('distance'))}")
                step_no += 1
            bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
            for line in bus.get("buslines") or []:
                if not isinstance(line, dict):
                    continue
                name = str(line.get("name") or "公交").split("（", 1)[0]
                depart = (line.get("departure_stop") or {}).get("name") if isinstance(line.get("departure_stop"), dict) else ""
                arrive = (line.get("arrival_stop") or {}).get("name") if isinstance(line.get("arrival_stop"), dict) else ""
                lines.append(f"{step_no}. 乘坐 {name}：{depart} → {arrive}")
                step_no += 1
    return "\n".join(lines), cost


def _format_bike(data: dict[str, Any], origin: dict[str, str], destination: dict[str, str]) -> tuple[str, float]:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    paths = payload.get("paths") if isinstance(payload, dict) else None
    if not paths:
        _fail("AMAP_ERROR", "没有可用的骑行路线。")
    path = paths[0]
    lines = [
        f"起点：{origin['name']}",
        f"终点：{destination['name']}",
        "出行方式：骑行",
        f"距离：{_distance(path.get('distance'))}",
        f"预计耗时：{_duration(path.get('duration'))}",
        "主要路段：",
        *_steps(path.get("steps") or []),
    ]
    return "\n".join(lines), 0.0


def _format_path(
    data: dict[str, Any],
    origin: dict[str, str],
    destination: dict[str, str],
    mode: str,
    waypoints: list[dict[str, str]] | None,
) -> tuple[str, float]:
    route = data.get("route") if isinstance(data.get("route"), dict) else {}
    paths = route.get("paths") or []
    if not paths:
        _fail("AMAP_ERROR", "没有可用的路线方案。")
    path = paths[0]
    lines = [
        f"起点：{origin['name']}",
        f"终点：{destination['name']}",
        f"出行方式：{_MODE_LABELS.get(mode, mode)}",
    ]
    if waypoints:
        lines.append("途经：" + " → ".join(item["name"] for item in waypoints))
    lines.append(f"距离：{_distance(path.get('distance'))}")
    lines.append(f"预计耗时：{_duration(path.get('duration'))}")
    tolls = _money(path.get("tolls"))
    taxi = _money(route.get("taxi_cost"))
    cost = tolls
    if mode == "driving":
        if taxi > 0:
            lines.append(f"打车约 {taxi:g} 元")
            cost = taxi
        elif tolls > 0:
            lines.append(f"过路费约 {tolls:g} 元")
        else:
            lines.append("这次没有返回打车费用。")
    steps = _steps(path.get("steps") or [])
    if steps:
        lines.append("主要路段：")
        lines.extend(steps)
    return "\n".join(lines), cost


def _plan_day(
    client: httpx.Client,
    *,
    city: str,
    keywords: str,
    days: int,
    mode: str,
    api_key: str,
    secret: str,
) -> tuple[str, list[str], float]:
    query = keywords or "风景名胜"
    pois = _search_pois(client, query, city, api_key, secret, limit=min(4 * days, 8))
    if not pois:
        _fail("PLACE_NOT_FOUND", f"在「{city or '全国'}」找不到与「{query}」相关的地点。", api_key)
    lines = [
        f"城市：{city or pois[0].get('city') or '—'}",
        f"主题：{query}",
        f"建议天数：{days} 天",
        "推荐地点：",
    ]
    per_day = max(1, (len(pois) + days - 1) // days)
    for index, poi in enumerate(pois):
        day = min(days, index // per_day + 1)
        extra = f"；{poi['type']}" if poi.get("type") else ""
        lines.append(f"第{day}天 {index + 1}. {poi['name']}（{poi['address']}{extra}）")
    cost = 0.0
    if len(pois) >= 2:
        route_mode = "driving" if mode == "transit" else mode
        origin, *middle = pois
        destination = middle[-1] if middle else pois[-1]
        via = middle[:-1] if route_mode == "driving" else []
        try:
            routed = _plan_route(client, origin, destination, route_mode, api_key, secret, via or None)
            summary, cost = _format_route(routed, origin, destination, route_mode, via or None)
            lines.append("")
            lines.append("串联路线（按推荐顺序）：")
            lines.append(summary)
        except ValueError as exc:
            lines.append("")
            lines.append("串联路线未能生成，已仅返回地点列表。")
            lines.append(str(exc))
    return "\n".join(lines), [poi["name"] for poi in pois], cost


def _fail(code: str, message: str, api_key: str = "") -> NoReturn:
    raise ValueError(f"[{code}] {_scrub(message, api_key)}")


def _scrub(text: str, api_key: str = "") -> str:
    cleaned = text
    if api_key:
        cleaned = cleaned.replace(api_key, "***")
    return _KEY_PARAM.sub(r"\1***", cleaned)
