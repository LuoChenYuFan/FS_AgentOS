"""规划行程的入口。实现在 skills/规划行程。"""

from __future__ import annotations

from typing import Any

import httpx

from fs_agent_runtime.skills.loader import skill_module

_MODULE = None


def _module() -> Any:
    global _MODULE
    if _MODULE is None:
        _MODULE = skill_module("规划行程")
    return _MODULE


def plan_trip(
    arguments: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
    api_key: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    return _module().plan_trip(arguments, client=client, api_key=api_key, secret=secret)


def amap_api_key() -> str:
    return _module().amap_api_key()
