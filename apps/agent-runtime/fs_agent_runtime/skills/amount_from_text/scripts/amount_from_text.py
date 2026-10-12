"""从制度原文里取出金额。"""

from __future__ import annotations

import re
from typing import Any

_WINDOW = re.compile(r"(?:标准|报销|上限|不超过|低于)[^\d]{0,12}(\d+(?:\.\d+)?)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def run(arguments: dict[str, Any] | None = None) -> dict[str, float]:
    """技能入口。arguments 用 text，可以是一段文字或多段列表。"""
    arguments = arguments or {}
    return {"amount": _amount(arguments.get("text"))}


def _amount(text: object) -> float:
    if isinstance(text, list):
        raw = "\n".join(str(item) for item in text)
    else:
        raw = str(text or "")
    window = _WINDOW.search(raw)
    if window:
        return float(window.group(1))
    found = _NUMBER.search(raw)
    return float(found.group(0)) if found else 0.0
