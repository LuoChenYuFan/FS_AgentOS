"""Turn a model reply into one JSON object. Field checks stay with Pydantic."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def loads_json_object(raw: str) -> dict[str, Any]:
    """Drop greetings and a trailing comma, then parse one object."""

    body = _json_body(raw)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(_parse_detail(body)) from exc
    if not isinstance(payload, dict):
        raise TypeError("返回的不是 JSON 对象")
    return payload


def _json_body(raw: str) -> str:
    text = _FENCE.sub("", raw or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("没有找到 JSON 对象")
    body = text[start : end + 1]
    previous = None
    while previous != body:
        previous = body
        body = _TRAILING_COMMA.sub(r"\1", body)
    return body


def _parse_detail(body: str) -> str:
    if re.search(r"'\s*:", body) or re.search(r":\s*'", body):
        return "单引号无法解析"
    if _TRAILING_COMMA.search(body):
        return "尾随逗号无法解析"
    return "JSON 无法解析"
