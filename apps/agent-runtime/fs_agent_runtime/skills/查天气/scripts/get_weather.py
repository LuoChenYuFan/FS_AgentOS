"""查天气的执行脚本。返回 summary、temp、condition。"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

import httpx

GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
DAILY_URL = "https://api.open-meteo.com/v1/forecast"
AMAP_GEO_URL = "https://restapi.amap.com/v3/geocode/geo"
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
OPEN_METEO_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_AMAP_ADCODES = {"香港": "810000", "澳门": "820000", "台北": "710000"}
_HTTP_TIMEOUT = 15.0
_HORIZON_DAYS = 10
HORIZON_MESSAGE = "查询的时间比较长，暂时只支持查询10天内的天气情况"
_PAST_MESSAGE = "这个日期已经过去了，暂时只支持查询今天起 10 天内的天气。"
_APPID = re.compile(r"(appid=)[^&\s\"']+")
_AMAP_KEY = re.compile(r"(key=)[^&\s\"']+")

_CITY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("上海", ("shanghai", "shang hai", "上海", "上海市")),
    ("北京", ("beijing", "peking", "北京", "北京市", "peking city")),
    ("广州", ("guangzhou", "guang zhou", "canton", "广州", "广州市")),
    ("深圳", ("shenzhen", "shen zhen", "深圳", "深圳市")),
    ("杭州", ("hangzhou", "hang zhou", "杭州", "杭州市")),
    ("成都", ("chengdu", "cheng du", "成都", "成都市")),
    ("重庆", ("chongqing", "chong qing", "重庆", "重庆市")),
    ("天津", ("tianjin", "tian jin", "天津", "天津市")),
    ("武汉", ("wuhan", "wu han", "武汉", "武汉市")),
    ("西安", ("xi'an", "xian", "xi an", "西安", "西安市")),
    ("南京", ("nanjing", "nan jing", "南京", "南京市")),
    ("苏州", ("suzhou", "su zhou", "苏州", "苏州市")),
    ("长沙", ("changsha", "chang sha", "长沙", "长沙市")),
    ("郑州", ("zhengzhou", "zheng zhou", "郑州", "郑州市")),
    ("青岛", ("qingdao", "qing dao", "青岛", "青岛市")),
    ("大连", ("dalian", "da lian", "大连", "大连市")),
    ("宁波", ("ningbo", "ning bo", "宁波", "宁波市")),
    ("厦门", ("xiamen", "xia men", "amoy", "厦门", "厦门市")),
    ("沈阳", ("shenyang", "shen yang", "沈阳", "沈阳市")),
    ("哈尔滨", ("harbin", "haerbin", "哈尔滨", "哈尔滨市")),
    ("长春", ("changchun", "chang chun", "长春", "长春市")),
    ("济南", ("jinan", "ji nan", "济南", "济南市")),
    ("石家庄", ("shijiazhuang", "shi jia zhuang", "石家庄", "石家庄市")),
    ("太原", ("taiyuan", "tai yuan", "太原", "太原市")),
    ("合肥", ("hefei", "he fei", "合肥", "合肥市")),
    ("福州", ("fuzhou", "fu zhou", "福州", "福州市")),
    ("南昌", ("nanchang", "nan chang", "南昌", "南昌市")),
    ("南宁", ("nanning", "nan ning", "南宁", "南宁市")),
    ("昆明", ("kunming", "kun ming", "昆明", "昆明市")),
    ("贵阳", ("guiyang", "gui yang", "贵阳", "贵阳市")),
    ("海口", ("haikou", "hai kou", "海口", "海口市")),
    ("三亚", ("sanya", "san ya", "三亚", "三亚市")),
    ("兰州", ("lanzhou", "lan zhou", "兰州", "兰州市")),
    ("西宁", ("xining", "xi ning", "西宁", "西宁市")),
    ("银川", ("yinchuan", "yin chuan", "银川", "银川市")),
    ("乌鲁木齐", ("urumqi", "wulumuqi", "乌鲁木齐", "乌鲁木齐市")),
    ("拉萨", ("lhasa", "lasa", "拉萨", "拉萨市")),
    ("呼和浩特", ("hohhot", "huhehaote", "呼和浩特", "呼和浩特市")),
    ("香港", ("hong kong", "hongkong", "hk", "香港")),
    ("澳门", ("macau", "macao", "澳门")),
    ("台北", ("taipei", "tai pei", "台北", "台北市")),
    ("高雄", ("kaohsiung", "gaoxiong", "高雄", "高雄市")),
    ("无锡", ("wuxi", "wu xi", "无锡", "无锡市")),
    ("佛山", ("foshan", "fo shan", "佛山", "佛山市")),
    ("东莞", ("dongguan", "dong guan", "东莞", "东莞市")),
    ("珠海", ("zhuhai", "zhu hai", "珠海", "珠海市")),
    ("中山", ("zhongshan", "zhong shan", "中山", "中山市")),
    ("温州", ("wenzhou", "wen zhou", "温州", "温州市")),
    ("常州", ("changzhou", "chang zhou", "常州", "常州市")),
    ("烟台", ("yantai", "yan tai", "烟台", "烟台市")),
    ("桂林", ("guilin", "gui lin", "桂林", "桂林市")),
    ("丽江", ("lijiang", "li jiang", "丽江", "丽江市")),
)

_PLACE_SUFFIXES = (
    "特别行政区",
    "地区",
    "市区",
    "市",
    "省",
    "县",
    " city",
    " municipality",
    " prefecture",
)

_NAMED_OFFSETS = {
    "today": 0,
    "now": 0,
    "current": 0,
    "当前": 0,
    "现在": 0,
    "今天": 0,
    "今日": 0,
    "今晚": 0,
    "tomorrow": 1,
    "明天": 1,
    "明日": 1,
    "明晚": 1,
    "后天": 2,
    "后日": 2,
    "大后天": 3,
    "yesterday": -1,
    "昨天": -1,
    "前天": -2,
}
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_RELATIVE_DAYS = re.compile(r"^(\d+|[零一二三四五六七八九十两]+)天(?:后|以后|之后)$")
_WEEKDAY = re.compile(r"^(?P<prefix>下下下|下下|下|这)?(?:周|星期|礼拜)(?P<day>[一二三四五六日天])$")
_YMD = re.compile(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$")
_MONTH_DAY = re.compile(r"^(\d{1,2})月(\d{1,2})日?$")
_WMO = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "小阵雨",
    81: "中阵雨",
    82: "大阵雨",
    85: "小阵雪",
    86: "大阵雪",
    95: "雷暴",
}


def load_local_env() -> None:
    """Fill unset variables from the project .env. Never logs values."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        env_file = parent / ".env"
        if env_file.is_file() and (parent / "pyproject.toml").is_file():
            _apply_env(env_file)
            return


def openweather_api_key() -> str:
    load_local_env()
    return os.environ.get("OPENWEATHER_API_KEY", "").strip()


def amap_weather_api_key() -> str:
    load_local_env()
    return os.environ.get("AMAP_TRAFFIC_API_KEY", "").strip()


def run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """技能入口。arguments 用 城市、日期；没写日期则查今天。"""
    return lookup_weather(arguments)


def lookup_weather(
    arguments: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Query current weather, or a daily forecast inside the next 10 days."""
    arguments = arguments or {}
    city = str(arguments.get("城市") or arguments.get("city") or arguments.get("location") or "").strip()
    when_raw = str(
        arguments.get("日期") or arguments.get("when") or arguments.get("date") or arguments.get("day") or ""
    ).strip()
    if not city:
        _fail("MISSING_CITY", "未提供城市名。请询问用户要查哪个城市，不要猜测。")
    offset = _resolve_offset(when_raw, datetime.now().astimezone().date())
    _reject_offset(offset)
    owns_client = client is None
    http = client or httpx.Client(timeout=_HTTP_TIMEOUT)
    try:
        if api_key is None:
            amap_key = amap_weather_api_key()
            if amap_key:
                try:
                    return _lookup_amap(http, city, offset, amap_key)
                except ValueError as exc:
                    if "[AMAP_ERROR]" not in str(exc) and "[CITY_NOT_FOUND]" not in str(exc):
                        raise
                    return _lookup_open_meteo(http, city, offset)
        key = openweather_api_key() if api_key is None else api_key.strip()
        if not key:
            _fail("MISSING_API_KEY", "未配置 OPENWEATHER_API_KEY。请在 .env 中填入 OpenWeatherMap 密钥。")
        return _lookup(http, city, offset, key)
    except httpx.TimeoutException:
        _fail("BUSY", "当前远端服务繁忙，请稍后再试。")
    except httpx.HTTPStatusError as exc:
        _fail("HTTP_STATUS", f"天气服务返回 HTTP {exc.response.status_code}。")
    except httpx.HTTPError as exc:
        _fail("NETWORK", f"网络错误（{_scrub(str(exc))}）。")
    finally:
        if owns_client:
            http.close()


def _lookup_amap(client: httpx.Client, city: str, offset: int, api_key: str) -> dict[str, Any]:
    adcode = _amap_adcode(client, city, api_key)
    payload = _get_json(
        client,
        AMAP_WEATHER_URL,
        {"key": api_key, "city": adcode, "extensions": "all"},
    )
    if not isinstance(payload, dict) or str(payload.get("status")) != "1":
        info = str(payload.get("info") or payload.get("infocode") or "未知") if isinstance(payload, dict) else "未知"
        _fail("AMAP_ERROR", f"高德天气查询失败（{info}）。")
    forecasts = payload.get("forecasts") if isinstance(payload.get("forecasts"), list) else []
    if not forecasts or not isinstance(forecasts[0], dict):
        _fail("FORECAST_NOT_FOUND", f"没有「{city}」的天气预报。")
    forecast = forecasts[0]
    location = str(forecast.get("city") or city)
    casts = forecast.get("casts") if isinstance(forecast.get("casts"), list) else []
    if offset >= len(casts) or not isinstance(casts[offset], dict):
        _fail("FORECAST_NOT_FOUND", "没有这一天的预报。请把这句话告诉用户，不要用同一地点重试。")
    day = casts[offset]
    description = str(day.get("dayweather") or day.get("nightweather") or "未知")
    high = day.get("daytemp")
    low = day.get("nighttemp")
    wind = str(day.get("daywind") or "—")
    power = str(day.get("daypower") or "")
    wind_text = f"{wind}风 {power}级" if power else wind
    label = _period_label(offset, date.fromisoformat(str(day.get("date")))) if str(day.get("date") or "").count("-") == 2 else _period_label(offset, datetime.now().date())
    summary = (
        f"地点：{location}\n"
        f"时段：{label}\n"
        f"天气：{description}\n"
        f"最高气温：{high}°C\n"
        f"最低气温：{low}°C\n"
        f"风向：{wind_text}"
    )
    return {"summary": summary, "temp": _required_temp(high, label), "condition": description}


def _amap_adcode(client: httpx.Client, city: str, api_key: str) -> str:
    text = city.strip()
    if text.isdigit() and len(text) in {6, 9}:
        return text
    known = _AMAP_ADCODES.get(_canonical_city(text) or text)
    if known:
        return known
    query = _canonical_city(text) or text
    payload = _get_json(client, AMAP_GEO_URL, {"key": api_key, "address": query})
    if not isinstance(payload, dict) or str(payload.get("status")) != "1":
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」。请确认地名，不要用同一城市名重试。")
    geos = payload.get("geocodes") if isinstance(payload.get("geocodes"), list) else []
    if not geos or not isinstance(geos[0], dict):
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」。请确认地名，不要用同一城市名重试。")
    adcode = str(geos[0].get("adcode") or "").strip()
    if not adcode:
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」的行政区编码。")
    return adcode


def _lookup(client: httpx.Client, city: str, offset: int, api_key: str) -> dict[str, Any]:
    query = _city_geocode_query(city) or city
    expected = _city_country(city)
    geo = _get_json(client, GEO_URL, {"q": query, "limit": 5, "appid": api_key})
    if not isinstance(geo, list) or not geo:
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」。请确认地名，不要用同一城市名重试。")
    place = _pick_geo_place(geo, expected_country=expected)
    country = str(place.get("country") or "")
    if expected and country.upper() != expected:
        _fail("CITY_NOT_FOUND", f"找不到「{city}」在目标国家（{expected}）的地点，请确认地名。")
    lat = place.get("lat")
    lon = place.get("lon")
    if lat is None or lon is None:
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」的坐标。")
    local_names = place.get("local_names") if isinstance(place.get("local_names"), dict) else {}
    place_name = str(local_names.get("zh") or place.get("name") or city)
    admin = str(place.get("state") or "")
    location = " ".join(part for part in (place_name, admin, country) if part)
    params = {"lat": lat, "lon": lon, "appid": api_key, "units": "metric", "lang": "zh_cn"}
    if offset > 0:
        forecast = _get_json(client, FORECAST_URL, params)
        if not isinstance(forecast, dict):
            _fail("FORECAST_NOT_FOUND", "没有这一天的预报。请把这句话告诉用户，不要用同一地点重试。")
        found = _forecast_result(forecast, location, offset)
        if found is not None:
            return found
        return _daily_result(client, float(lat), float(lon), offset, location)
    current = _get_json(client, WEATHER_URL, params)
    if not isinstance(current, dict):
        _fail("HTTP_STATUS", "天气数据格式无法识别。")
    return _current_result(current, location)


def _lookup_open_meteo(client: httpx.Client, city: str, offset: int) -> dict[str, Any]:
    canonical = _canonical_city(city) or city
    query = {"香港": "Hong Kong", "澳门": "Macau", "台北": "Taipei"}.get(canonical, canonical)
    payload = _get_json(client, OPEN_METEO_GEO_URL, {"name": query, "count": 1, "language": "zh"})
    results = payload.get("results") if isinstance(payload, dict) and isinstance(payload.get("results"), list) else []
    if not results or not isinstance(results[0], dict):
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」。请确认地名，不要用同一城市名重试。")
    place = results[0]
    lat = place.get("latitude")
    lon = place.get("longitude")
    if lat is None or lon is None:
        _fail("CITY_NOT_FOUND", f"找不到城市「{city}」的坐标。")
    location = str(place.get("name") or city)
    admin = str(place.get("admin1") or "")
    country = str(place.get("country") or "")
    label = " ".join(part for part in (location, admin, country) if part)
    return _daily_result(client, float(lat), float(lon), offset, label)


def _current_result(data: dict[str, Any], location: str) -> dict[str, Any]:
    description = _description(data)
    main = data.get("main") if isinstance(data.get("main"), dict) else {}
    wind = data.get("wind") if isinstance(data.get("wind"), dict) else {}
    temp = _required_temp(main.get("temp"), "今天实况")
    summary = (
        f"地点：{location}\n"
        f"时段：今天实况\n"
        f"天气：{description}\n"
        f"气温：{main.get('temp', '—')}°C（体感 {main.get('feels_like', '—')}°C）\n"
        f"湿度：{main.get('humidity', '—')}%\n"
        f"风速：{wind.get('speed', '—')} m/s"
    )
    return {"summary": summary, "temp": temp, "condition": description}


def _forecast_result(data: dict[str, Any], location: str, offset: int) -> dict[str, Any] | None:
    city = data.get("city") if isinstance(data.get("city"), dict) else {}
    tz = timezone(timedelta(seconds=int(city.get("timezone") or 0)))
    target = datetime.now(tz).date() + timedelta(days=offset)
    items: list[dict[str, Any]] = []
    for item in data.get("list") or []:
        if not isinstance(item, dict) or "dt" not in item:
            continue
        stamp = datetime.fromtimestamp(int(item["dt"]), tz=tz)
        if stamp.date() == target:
            items.append(item)
    if not items:
        return None
    label = _period_label(offset, target)
    temps = [
        float(item["main"]["temp"])
        for item in items
        if isinstance(item.get("main"), dict) and item["main"].get("temp") is not None
    ]
    mid = min(items, key=lambda item: abs(datetime.fromtimestamp(int(item["dt"]), tz=tz).hour - 12))
    description = _description(mid)
    wind = mid.get("wind") if isinstance(mid.get("wind"), dict) else {}
    midday = (mid.get("main") or {}).get("temp", "—") if isinstance(mid.get("main"), dict) else "—"
    high = max(temps) if temps else "—"
    low = min(temps) if temps else "—"
    return _day_result(location, label, description, high, low, midday, wind.get("speed", "—"))


def _daily_result(client: httpx.Client, lat: float, lon: float, offset: int, location: str) -> dict[str, Any]:
    payload = _get_json(
        client,
        DAILY_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "hourly": "temperature_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
            "forecast_days": _HORIZON_DAYS,
            "wind_speed_unit": "ms",
        },
    )
    if not isinstance(payload, dict):
        _fail("FORECAST_NOT_FOUND", "没有这一天的预报。请把这句话告诉用户，不要用同一地点重试。")
    daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
    days = daily.get("time") if isinstance(daily.get("time"), list) else []
    if offset >= len(days):
        _fail("FORECAST_NOT_FOUND", "没有这一天的预报。请把这句话告诉用户，不要用同一地点重试。")
    target = str(days[offset])
    label = _period_label(offset, date.fromisoformat(target))
    noon = _noon_hour(payload.get("hourly"), target)
    codes = daily.get("weather_code") if isinstance(daily.get("weather_code"), list) else []
    highs = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
    lows = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
    code = noon.get("weather_code", codes[offset] if offset < len(codes) else None)
    description = _WMO.get(int(code), "未知") if code is not None else "未知"
    midday = noon.get("temperature_2m", highs[offset] if offset < len(highs) else None)
    wind = noon.get("wind_speed_10m", "—")
    high = highs[offset] if offset < len(highs) else "—"
    low = lows[offset] if offset < len(lows) else "—"
    return _day_result(location, label, description, high, low, midday, wind)


def _noon_hour(hourly: object, target: str) -> dict[str, Any]:
    if not isinstance(hourly, dict):
        return {}
    times = hourly.get("time") if isinstance(hourly.get("time"), list) else []
    chosen: tuple[int, int] | None = None
    for index, stamp in enumerate(times):
        text = str(stamp)
        if not text.startswith(target):
            continue
        hour = int(text[11:13]) if len(text) >= 13 else 0
        if chosen is None or abs(hour - 12) < abs(chosen[0] - 12):
            chosen = (hour, index)
    if chosen is None:
        return {}
    index = chosen[1]
    found: dict[str, Any] = {}
    for key in ("temperature_2m", "weather_code", "wind_speed_10m"):
        values = hourly.get(key)
        if isinstance(values, list) and index < len(values):
            found[key] = values[index]
    return found


def _day_result(
    location: str,
    label: str,
    description: str,
    high: object,
    low: object,
    midday: object,
    wind: object,
) -> dict[str, Any]:
    summary = (
        f"地点：{location}\n"
        f"时段：{label}\n"
        f"天气：{description}\n"
        f"最高气温：{high}°C\n"
        f"最低气温：{low}°C\n"
        f"中午气温：{midday}°C\n"
        f"风速：{wind} m/s"
    )
    return {"summary": summary, "temp": _required_temp(midday, label), "condition": description}


def _period_label(offset: int, target: date) -> str:
    if offset == 1:
        return "明天预报"
    if offset == 2:
        return "后天预报"
    if offset == 3:
        return "大后天预报"
    return f"{target.month}月{target.day}日预报"


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> Any:
    response = client.get(url, params=params)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        _fail("HTTP_STATUS", "OpenWeatherMap 返回的不是 JSON。")


def _description(data: dict[str, Any]) -> str:
    weather_list = data.get("weather")
    if isinstance(weather_list, list) and weather_list and isinstance(weather_list[0], dict):
        return str(weather_list[0].get("description") or "未知")
    return "未知"


def _required_temp(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        _fail("FORECAST_NOT_FOUND", f"没有「{label}」的气温。请把这句话告诉用户，不要用同一地点重试。")
    try:
        return float(value)
    except ValueError:
        _fail("FORECAST_NOT_FOUND", f"没有「{label}」的气温。请把这句话告诉用户，不要用同一地点重试。")


def _fail(code: str, message: str) -> NoReturn:
    raise ValueError(f"[{code}] {message}")


def _scrub(text: str) -> str:
    return _AMAP_KEY.sub(r"\1***", _APPID.sub(r"\1***", text))


def _apply_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return " ".join(text.strip().split()).casefold()


def _strip_place_suffix(text: str) -> str:
    current = text
    for suffix in _PLACE_SUFFIXES:
        if current.endswith(suffix):
            current = current[: -len(suffix)].strip()
    return current


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for canonical, variants in _CITY_GROUPS:
        for variant in (*variants, canonical):
            key = _normalize_text(variant)
            aliases[key] = canonical
            stripped = _strip_place_suffix(key)
            if stripped:
                aliases[stripped] = canonical
    return aliases


_CITY_ALIASES = _build_aliases()
_CITY_COUNTRIES: dict[str, str] = {name: "CN" for name, _ in _CITY_GROUPS}
_CITY_COUNTRIES.update({"香港": "HK", "澳门": "MO", "台北": "TW", "高雄": "TW"})


def _reject_offset(offset: int) -> None:
    if offset < 0:
        _fail("INVALID_WHEN", _PAST_MESSAGE)
    if offset >= _HORIZON_DAYS:
        _fail("HORIZON", HORIZON_MESSAGE)


def _resolve_offset(raw: str, today: date) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    named = _NAMED_OFFSETS.get(text)
    if named is None:
        named = _NAMED_OFFSETS.get(_normalize_text(text))
    if named is not None:
        return named
    relative = _RELATIVE_DAYS.fullmatch(text)
    if relative:
        count = _cn_to_int(relative.group(1))
        if count is None:
            _fail("INVALID_WHEN", f"看不懂日期「{raw}」。可以说今天、明天、后天，或 10 天内的具体日期。")
        return count
    weekday = _weekday_offset(text, today)
    if weekday is not None:
        return weekday
    absolute = _absolute_offset(text, today)
    if absolute is not None:
        return absolute
    _fail("INVALID_WHEN", f"看不懂日期「{raw}」。可以说今天、明天、后天，或 10 天内的具体日期。")


def _cn_to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2 and text[1] in _CN_DIGITS:
        return 10 + _CN_DIGITS[text[1]]
    if "十" in text:
        left, _, right = text.partition("十")
        if left in _CN_DIGITS and (right == "" or right in _CN_DIGITS):
            return _CN_DIGITS[left] * 10 + (_CN_DIGITS[right] if right else 0)
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


def _weekday_offset(text: str, today: date) -> int | None:
    matched = _WEEKDAY.fullmatch(text)
    if matched is None:
        return None
    weekday = _WEEKDAY_INDEX[matched.group("day")]
    prefix = matched.group("prefix")
    if prefix is None:
        return (weekday - today.weekday()) % 7
    weeks = {"这": 0, "下": 1, "下下": 2, "下下下": 3}[prefix]
    monday = today - timedelta(days=today.weekday())
    target = monday + timedelta(days=weeks * 7 + weekday)
    return (target - today).days


def _absolute_offset(text: str, today: date) -> int | None:
    dated = _YMD.fullmatch(text) or _MONTH_DAY.fullmatch(text)
    if dated is None:
        return None
    numbers = [int(part) for part in dated.groups()]
    try:
        if len(numbers) == 3:
            target = date(numbers[0], numbers[1], numbers[2])
        else:
            target = date(today.year, numbers[0], numbers[1])
            if target < today:
                target = date(today.year + 1, numbers[0], numbers[1])
    except ValueError:
        return None
    return (target - today).days


def _canonical_city(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    key = _normalize_text(text)
    if key in _CITY_ALIASES:
        return _CITY_ALIASES[key]
    stripped = _strip_place_suffix(key)
    if stripped in _CITY_ALIASES:
        return _CITY_ALIASES[stripped]
    return text


def _city_country(raw: str) -> str:
    return _CITY_COUNTRIES.get(_canonical_city(raw), "")


def _latin_alias(canonical: str) -> str:
    for name, variants in _CITY_GROUPS:
        if name != canonical:
            continue
        for variant in variants:
            compact = variant.replace(" ", "").replace("'", "")
            if compact.isascii() and any(ch.isalpha() for ch in compact):
                titled = variant.title() if variant.islower() else variant
                return titled
    return canonical


def _city_geocode_query(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    canonical = _canonical_city(text) or text
    country = _city_country(canonical)
    name = _latin_alias(canonical) if country else canonical
    if country:
        return f"{name},{country}"
    return name


def _pick_geo_place(places: list[dict[str, Any]], *, expected_country: str = "") -> dict[str, Any]:
    if not places:
        raise ValueError("places 不能为空")
    wanted = (expected_country or "").strip().upper()
    if wanted:
        for place in places:
            if str(place.get("country") or "").upper() == wanted:
                return place
    return places[0]
