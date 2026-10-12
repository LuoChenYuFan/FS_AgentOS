"""查阅内部知识库。编排器把已经接好的资料库查询函数传进来。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Query = Callable[[dict[str, Any]], dict[str, Any]]


def run(arguments: dict[str, Any] | None = None, query: Query | None = None) -> dict[str, Any]:
    """技能入口。query 由编排器注入，脚本负责保持检索结果的资料角色。"""
    arguments = arguments or {}
    if query is None:
        raise RuntimeError("rag_query is not connected")
    found = query(arguments)
    if not isinstance(found, dict):
        raise TypeError("rag_query did not return an object")
    context = found.get("retrieved_context")
    if isinstance(context, dict) and context.get("role") == "data":
        return found
    return {
        **found,
        "retrieved_context": {
            "role": "data",
            "query": str(arguments.get("query") or ""),
            "citations": [],
        },
    }
