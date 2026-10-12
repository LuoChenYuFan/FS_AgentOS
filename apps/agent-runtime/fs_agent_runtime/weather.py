"""查天气的旧入口。实现已挪到 skills/查天气。"""

from __future__ import annotations

from typing import Any

import httpx

from fs_agent_runtime.skills.loader import skill_module

_MODULE = None


def _module() -> Any:
    global _MODULE
    if _MODULE is None:
        _MODULE = skill_module("查天气")
    return _MODULE


def lookup_weather(
    arguments: dict[str, Any] | None = None,
    *,
    client: httpx.Client | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    return _module().lookup_weather(arguments, client=client, api_key=api_key)


def openweather_api_key() -> str:
    return _module().openweather_api_key()
