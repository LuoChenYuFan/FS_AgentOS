"""把两个已经查出的数字相减。"""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any] | None = None) -> dict[str, float]:
    """技能入口。arguments 用 left、right。"""
    arguments = arguments or {}
    return {"value": float(arguments.get("left") or 0) - float(arguments.get("right") or 0)}
