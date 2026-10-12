"""查酒店的执行脚本。真实酒店接口接入前，返回固定均价。"""

from __future__ import annotations

from typing import Any

_AVG_PRICE = 1850.0


def run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """技能入口。arguments 用 城市、星级、日期。"""
    del arguments
    return {"hotels": [], "avg_price": _AVG_PRICE, "count": 12}
