"""The orchestrator calls this skill. Agents do not."""

from __future__ import annotations

from typing import Any

from fs_rag_sdk import RetrieveRequest
from fs_rag_service import RagService


class RagQuerySkill:
    def __init__(self, library: RagService) -> None:
        self._library = library

    def query(self, *, query: str, tenant_id: str, top_k: int, scope_filter: str) -> dict[str, Any]:
        del scope_filter
        result = self._library.retrieve_blocking(
            RetrieveRequest(query=query, tenant_id=tenant_id, top_k=top_k)
        )
        citations = result.retrieved_context.citations if result.retrieved_context else result.citations
        confidence = citations[0].score if citations else 0.0
        return {
            "chunks": [item.text for item in citations],
            "sources": [item.source_uri or item.url or "" for item in citations],
            "confidence": confidence,
            "retrieved_context": {
                "role": "data",
                "query": query,
                "citations": [item.model_dump() for item in citations],
            },
        }
