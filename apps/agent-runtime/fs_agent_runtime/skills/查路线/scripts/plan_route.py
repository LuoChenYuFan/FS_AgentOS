"""查路线的执行脚本。真实地图接入前，返回固定的耗时、距离和费用。"""

from __future__ import annotations

from typing import Any

_FARE = 35.0


def run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """技能入口。arguments 用 起点、终点、方式。"""
    del arguments
    return {"duration_min": 30, "distance_km": 12, "steps": [], "fare": _FARE}
