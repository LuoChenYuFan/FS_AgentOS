"""Turn a short follow-up into a full request using the dialogue slots.

Storage lives on the dialogue board. This module only reads those slots.
"""

from __future__ import annotations

from typing import Any

from fs_agent_runtime.planner import (
    _city,
    _recipient,
    _when,
    is_place_to_place,
    place_ends,
    spoken_city,
    trip_mode,
)

BUSY_REPLY = "当前远端服务繁忙，请稍后再试。"
NEED_PLACES_REPLY = "请告诉我从哪里到哪里。例如：从北京西站到北京南站，如果打车要多久、多少钱。"
NEED_WEATHER_CITY_REPLY = "请问要查询哪里的天气？"
WEATHER_CITY_NEEDED = "__need_weather_city__"

_FOLLOW_MODES = (
    "打车",
    "开车",
    "自驾",
    "驾车",
    "步行",
    "走路",
    "徒步",
    "骑行",
    "骑车",
    "地铁",
    "公交",
    "换乘",
)
_FOLLOW_CUES = ("如果", "换成", "改成", "改为", "要不", "或者", "多久", "多长时间", "多少钱", "费用", "用时")
_WHEN_WORDS = ("大后天", "后天", "明天", "今天", "昨日", "昨天")


def is_trip_followup(text: str) -> bool:
    if place_ends(text) is not None or is_place_to_place(text):
        return False
    if not any(word in text for word in _FOLLOW_MODES):
        return False
    return any(word in text for word in _FOLLOW_CUES)


def resolve_utterance(
    text: str,
    slots: dict[str, Any] | None,
    turns: list[dict[str, str]] | None = None,
) -> str | None:
    """None keeps the sentence. An empty string means the places are still unknown."""

    slots = slots or {}
    rewritten = _return_trip(text, slots) or _mode_change(text, slots)
    if rewritten is not None:
        return rewritten
    weather = _weather_city_answer(text, slots, turns) or _weather_followup(text, slots, turns)
    if weather is not None:
        return weather
    mailed = _mail_followup(text, slots)
    if mailed is not None:
        return mailed
    return None


def canned_reply(resolved: str | None) -> str | None:
    if resolved == "":
        return NEED_PLACES_REPLY
    if resolved == WEATHER_CITY_NEEDED:
        return NEED_WEATHER_CITY_REPLY
    return None


def slot_patch_from_text(text: str) -> dict[str, dict[str, str]]:
    patch: dict[str, dict[str, str]] = {}
    ends = place_ends(text)
    if ends is not None:
        origin, destination = ends
        patch["itinerary"] = {"origin": origin, "destination": destination, "mode": trip_mode(text)}
    if "天气" in text:
        patch["weather"] = {"date": _when(text)}
        city = _city(text, "")
        if city:
            patch["weather"]["city"] = city
    recipient = _recipient(text)
    if recipient:
        patch["email"] = {"recipient": recipient}
    return patch


def slot_patch_from_delivered(delivered: dict[Any, dict[str, Any]]) -> dict[str, dict[str, str]]:
    patch: dict[str, dict[str, str]] = {}
    for args in delivered.values():
        if not isinstance(args, dict):
            continue
        origin = str(args.get("起点") or "").strip()
        destination = str(args.get("终点") or "").strip()
        if origin and destination:
            patch["itinerary"] = {
                "origin": origin,
                "destination": destination,
                "mode": str(args.get("方式") or "").strip(),
            }
        city = str(args.get("城市") or "").strip()
        if city and "星级" not in args:
            patch["weather"] = {"city": city, "date": str(args.get("日期") or "").strip()}
        recipient = str(args.get("收件人") or "").strip()
        if recipient:
            patch["email"] = {"recipient": recipient, "subject": str(args.get("主题") or "").strip()}
        query = str(args.get("query") or "").strip()
        if query:
            patch["knowledge"] = {"subject": query[:32]}
    return patch


def is_busy_failure(message: str) -> bool:
    return message.startswith("[BUSY]") or "timed out" in message or "Timeout" in message


def _itinerary(slots: dict[str, Any]) -> tuple[str, str, str]:
    raw = slots.get("itinerary")
    if not isinstance(raw, dict):
        return "", "", ""
    return (
        str(raw.get("origin") or "").strip(),
        str(raw.get("destination") or "").strip(),
        str(raw.get("mode") or "").strip(),
    )


def _return_trip(text: str, slots: dict[str, Any]) -> str | None:
    if "回去" not in text and "返程" not in text:
        return None
    if place_ends(text) is not None:
        return None
    if not any(word in text for word in (*_FOLLOW_CUES, *_FOLLOW_MODES, "怎么", "路线")):
        return None
    origin, destination, mode = _itinerary(slots)
    if not origin or not destination:
        return ""
    travel = text
    if not any(word in text for word in _FOLLOW_MODES) and mode:
        travel = f"{mode}{text}"
    return f"从{destination}到{origin}{travel}"


def _mode_change(text: str, slots: dict[str, Any]) -> str | None:
    if not is_trip_followup(text):
        return None
    origin, destination, _mode = _itinerary(slots)
    if not origin or not destination:
        return ""
    return f"从{origin}到{destination}{text}"


def _weather_city_answer(
    text: str,
    slots: dict[str, Any],
    turns: list[dict[str, str]] | None,
) -> str | None:
    if not _awaiting_weather_city(turns):
        return None
    city = spoken_city(text)
    if not city:
        return WEATHER_CITY_NEEDED if "天气" in text else None
    date = _slot_weather_date(slots) or _when(text)
    return f"帮我查一下{city}{date}的天气"


def _weather_followup(
    text: str,
    slots: dict[str, Any],
    turns: list[dict[str, str]] | None = None,
) -> str | None:
    if "邮件" in text:
        return None
    named = spoken_city(text)
    if named:
        return None
    asking = "天气" in text
    dated = text.endswith("呢") and any(word in text for word in _WHEN_WORDS)
    if not asking and not dated:
        return None
    city = _city_from_context(slots, turns)
    if city:
        when = next((word for word in _WHEN_WORDS if word in text), None) or _when(text)
        return f"帮我查一下{city}{when}的天气"
    if asking:
        return WEATHER_CITY_NEEDED
    return None


def _city_from_context(slots: dict[str, Any], turns: list[dict[str, str]] | None) -> str:
    city = _slot_weather_city(slots)
    if city:
        return city
    for turn in reversed(turns or []):
        if turn.get("role") != "user":
            continue
        found = spoken_city(str(turn.get("text") or ""))
        if found:
            return found
    origin, destination, _mode = _itinerary(slots)
    for place in (destination, origin):
        found = spoken_city(place)
        if found:
            return found
    return ""


def _slot_weather_city(slots: dict[str, Any]) -> str:
    raw = slots.get("weather")
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("city") or "").strip()


def _slot_weather_date(slots: dict[str, Any]) -> str:
    raw = slots.get("weather")
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("date") or "").strip()


def _awaiting_weather_city(turns: list[dict[str, str]] | None) -> bool:
    for turn in reversed(turns or []):
        role = turn.get("role")
        if role == "assistant":
            return str(turn.get("text") or "") == NEED_WEATHER_CITY_REPLY
        if role == "user":
            return False
    return False


def _mail_followup(text: str, slots: dict[str, Any]) -> str | None:
    if "邮件" not in text or _recipient(text):
        return None
    raw = slots.get("email")
    recipient = str(raw.get("recipient") or "").strip() if isinstance(raw, dict) else ""
    if not recipient:
        return None
    return f"给{recipient}{text}"


# trip_mode is part of the public planning surface; re-exported for callers that
# already resolved a sentence and want the same mode label.
__all__ = [
    "BUSY_REPLY",
    "NEED_PLACES_REPLY",
    "NEED_WEATHER_CITY_REPLY",
    "WEATHER_CITY_NEEDED",
    "canned_reply",
    "is_busy_failure",
    "is_trip_followup",
    "resolve_utterance",
    "slot_patch_from_delivered",
    "slot_patch_from_text",
    "trip_mode",
]
