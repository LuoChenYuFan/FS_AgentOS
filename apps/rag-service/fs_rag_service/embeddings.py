"""Turn chunk and query text into vectors. The hash embedder is the offline stand-in."""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx
from fs_common.model_gate import model_gate

from fs_rag_service.errors import RetrievalBackendError
from fs_rag_service.textutil import embed

_TIMEOUT_SECONDS = 60.0
_BATCH_SIZE = 20
_DEFAULT_DIMENSIONS = 1024
_ALLOWED_DIMENSIONS = {2560, 2048, 1536, 1024, 768, 512, 256}


class HashEmbedder:
    dim = 32

    def embed(self, text: str) -> list[float]:
        return embed(text, self.dim)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    async def aembed(self, text: str) -> list[float]:
        return self.embed(text)

    async def aembed_many(self, texts: list[str]) -> list[list[float]]:
        return self.embed_many(texts)


class DashScopeEmbedder:
    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        model: str,
        dimensions: int = _DEFAULT_DIMENSIONS,
        timeout: float = _TIMEOUT_SECONDS,
        post: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self.dim = dimensions
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._url = f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings"
        self._post = post or httpx.post

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    async def aembed(self, text: str) -> list[float]:
        vectors = await self.aembed_many([text])
        return vectors[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            vectors.extend(self._request(texts[start : start + _BATCH_SIZE]))
        return vectors

    async def aembed_many(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            vectors.extend(await self._arequest(texts[start : start + _BATCH_SIZE]))
        return vectors

    def _payload(self, texts: list[str]) -> dict[str, object]:
        return {
            "model": self._model,
            "input": texts,
            "dimensions": self.dim,
            "encoding_format": "float",
        }

    def _request(self, texts: list[str]) -> list[list[float]]:
        with model_gate().blocking():
            try:
                response = self._post(
                    self._url,
                    headers=_headers(self._api_key),
                    json=self._payload(texts),
                    timeout=self._timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise RetrievalBackendError("embedding request failed") from exc
        return _vectors(payload, len(texts), self.dim)

    async def _arequest(self, texts: list[str]) -> list[list[float]]:
        async with model_gate().slot():
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._url,
                        headers=_headers(self._api_key),
                        json=self._payload(texts),
                    )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise RetrievalBackendError("embedding request failed") from exc
        return _vectors(payload, len(texts), self.dim)


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def dashscope_embedder_from_env() -> DashScopeEmbedder | None:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    workspace_id = os.environ.get("WORKSPACE_ID", "")
    model = os.environ.get("EMBEDDING_MODEL", "")
    if not api_key or not workspace_id or not model:
        return None
    raw_dimensions = os.environ.get("EMBEDDING_DIMENSIONS", "")
    dimensions = int(raw_dimensions) if raw_dimensions else _DEFAULT_DIMENSIONS
    if dimensions not in _ALLOWED_DIMENSIONS:
        raise ValueError("embedding dimensions are not supported by the model")
    return DashScopeEmbedder(
        api_key=api_key,
        workspace_id=workspace_id,
        model=model,
        dimensions=dimensions,
    )


def _vectors(payload: object, count: int, dim: int) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise RetrievalBackendError("embedding response was not an object")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RetrievalBackendError("embedding response did not include data")
    ordered: list[list[float] | None] = [None] * count
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        values = row.get("embedding")
        if not isinstance(index, int) or not 0 <= index < count or not isinstance(values, list):
            continue
        if len(values) != dim or not all(isinstance(value, int | float) for value in values):
            raise RetrievalBackendError("embedding vector has an unexpected length")
        ordered[index] = [float(value) for value in values]
    if any(vector is None for vector in ordered):
        raise RetrievalBackendError("embedding response did not cover every input")
    return [vector for vector in ordered if vector is not None]
