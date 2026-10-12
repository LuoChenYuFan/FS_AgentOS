"""The knowledge exit. Retrieval itself is the next layer; this only accepts the handoff."""

from __future__ import annotations

from typing import Protocol


class KnowledgeHandoff(Protocol):
    def accept(self, *, tenant_id: str, text: str, intent: str) -> dict[str, str]: ...


class AcceptKnowledge:
    def accept(self, *, tenant_id: str, text: str, intent: str) -> dict[str, str]:
        del tenant_id
        return {"status": "accepted", "route": "knowledge", "intent": intent, "query": text}
