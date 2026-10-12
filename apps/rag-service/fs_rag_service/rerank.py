"""Rerank fused hits with the Aliyun compatible rerank API."""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx
from fs_common.model_gate import model_gate

from fs_rag_service.errors import RetrievalBackendError
from fs_rag_service.memory import FusedHit

_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query."
_TIMEOUT_SECONDS = 30.0


class DashScopeReranker:
    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        model: str,
        instruct: str = _INSTRUCT,
        timeout: float = _TIMEOUT_SECONDS,
        post: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._instruct = instruct
        self._timeout = timeout
        self._url = f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-api/v1/reranks"
        self._post = post or httpx.post

    def rerank(self, query: str, hits: list[FusedHit]) -> list[FusedHit]:
        return self._finish(query, hits, self._request(query, hits))

    async def arerank(self, query: str, hits: list[FusedHit]) -> list[FusedHit]:
        return self._finish(query, hits, await self._arequest(query, hits))

    def _finish(self, query: str, hits: list[FusedHit], payload: object | None) -> list[FusedHit]:
        usable = [hit for hit in hits if hit.text.strip()]
        if len(usable) < 2 or payload is None:
            return list(hits)
        return _apply_ranking(usable, hits, _result_rows(payload))

    def _body(self, query: str, usable: list[FusedHit]) -> dict[str, object]:
        return {
            "model": self._model,
            "documents": [hit.text for hit in usable],
            "query": query,
            "top_n": len(usable),
            "instruct": self._instruct,
        }

    def _request(self, query: str, hits: list[FusedHit]) -> object | None:
        usable = [hit for hit in hits if hit.text.strip()]
        if len(usable) < 2:
            return None
        with model_gate().blocking():
            try:
                response = self._post(
                    self._url,
                    headers=_headers(self._api_key),
                    json=self._body(query, usable),
                    timeout=self._timeout,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise RetrievalBackendError("rerank request failed") from exc

    async def _arequest(self, query: str, hits: list[FusedHit]) -> object | None:
        usable = [hit for hit in hits if hit.text.strip()]
        if len(usable) < 2:
            return None
        async with model_gate().slot():
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._url,
                        headers=_headers(self._api_key),
                        json=self._body(query, usable),
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise RetrievalBackendError("rerank request failed") from exc


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def dashscope_reranker_from_env() -> DashScopeReranker | None:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    workspace_id = os.environ.get("WORKSPACE_ID", "")
    if not api_key or not workspace_id:
        return None
    return DashScopeReranker(
        api_key=api_key,
        workspace_id=workspace_id,
        model=os.environ.get("RERANK_MODEL", "qwen3-rerank"),
    )


def _result_rows(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise RetrievalBackendError("rerank response was not an object")
    rows = payload.get("results")
    if rows is None and isinstance(payload.get("output"), dict):
        rows = payload["output"].get("results")
    if not isinstance(rows, list):
        raise RetrievalBackendError("rerank response did not include results")
    return [row for row in rows if isinstance(row, dict)]


def _apply_ranking(
    usable: list[FusedHit],
    original: list[FusedHit],
    rows: list[dict[str, object]],
) -> list[FusedHit]:
    ordered: list[FusedHit] = []
    seen: set[str] = set()
    for row in rows:
        index = row.get("index")
        if not isinstance(index, int) or not 0 <= index < len(usable):
            continue
        hit = usable[index]
        score = row.get("relevance_score", row.get("score", hit.score))
        ordered.append(
            FusedHit(
                hit.chunk_id,
                float(score) if isinstance(score, int | float) else hit.score,
                list(hit.channels),
                hit.text,
            )
        )
        seen.add(hit.chunk_id)
    for hit in original:
        if hit.chunk_id not in seen:
            ordered.append(hit)
    return ordered
