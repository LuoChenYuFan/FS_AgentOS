"""RAG request and response types.

`retrieved_context` is data. Callers keep it out of the system prompt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 5
    tenant_id: str = ""
    workspace_id: str | None = None


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float = 0.0
    page: int | None = None
    url: str | None = None
    source_uri: str | None = None
    entity_id: str | None = None
    channels: list[str] = Field(default_factory=list)


class RetrievedContext(BaseModel):
    """Retrieved passages. `role` stays `data` so this cannot be read as an instruction."""

    role: Literal["data"] = "data"
    query: str
    citations: list[Citation] = Field(default_factory=list)


class RetrieveResult(BaseModel):
    citations: list[Citation] = Field(default_factory=list)
    retrieved_context: RetrievedContext | None = None
    cache_hit: bool = False
    channels: list[str] = Field(default_factory=list)
