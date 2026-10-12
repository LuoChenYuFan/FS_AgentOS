"""Outbound LLM calls must go through a gateway — stub for Phase 0."""

from __future__ import annotations

from fs_common import NotImplementedYet


class LlmGatewayClient:
    async def chat(self, *, model: str, messages: list[dict[str, str]]) -> dict[str, str]:
        raise NotImplementedYet("llm_gateway.chat")
