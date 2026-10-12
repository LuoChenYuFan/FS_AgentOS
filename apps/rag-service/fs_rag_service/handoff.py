"""Knowledge-route exit. The passages stay in retrieved_context."""

from __future__ import annotations

from fs_rag_sdk import RetrieveRequest

from fs_rag_service.service import RagService


class RetrievingKnowledge:
    def __init__(self, rag: RagService) -> None:
        self.rag = rag

    def accept(self, *, tenant_id: str, text: str, intent: str) -> dict[str, object]:
        result = self.rag.retrieve_blocking(RetrieveRequest(query=text, tenant_id=tenant_id))
        context = result.retrieved_context
        payload = context.model_dump(mode="json") if context is not None else {"role": "data", "query": text, "citations": []}
        return {
            "status": "accepted",
            "route": "knowledge",
            "intent": intent,
            "cache_hit": result.cache_hit,
            "channels": result.channels,
            "retrieved_context": payload,
        }

    async def aaccept(self, *, tenant_id: str, text: str, intent: str) -> dict[str, object]:
        result = await self.rag.retrieve(RetrieveRequest(query=text, tenant_id=tenant_id))
        context = result.retrieved_context
        payload = context.model_dump(mode="json") if context is not None else {"role": "data", "query": text, "citations": []}
        return {
            "status": "accepted",
            "route": "knowledge",
            "intent": intent,
            "cache_hit": result.cache_hit,
            "channels": result.channels,
            "retrieved_context": payload,
        }
