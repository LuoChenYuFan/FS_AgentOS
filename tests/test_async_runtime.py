"""Model calls wait together, and retrieval does not block the event loop."""

import asyncio

from fs_common.model_gate import ModelGate
from fs_llm_gateway import DashScopeChat
from fs_rag_sdk import RetrieveRequest
from fs_rag_service import build_memory_rag


def test_model_gate_lets_one_call_through_at_a_time() -> None:
    gate = ModelGate(1)
    peak = 0
    current = 0

    async def work() -> None:
        nonlocal peak, current
        async with gate.slot():
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.02)
            current -= 1

    async def scenario() -> None:
        await asyncio.gather(*(work() for _ in range(4)))

    asyncio.run(scenario())
    assert peak == 1


def test_async_chat_uses_the_injected_client() -> None:
    async def post(url: str, **kwargs: object) -> object:
        assert url.endswith("/chat/completions")
        assert kwargs["json"] == {"model": "qwen-test", "messages": [{"role": "user", "content": "hi"}]}
        return _Reply("ok")

    chat = DashScopeChat(
        api_key="test-key",
        workspace_id="workspace",
        model="qwen-test",
        apost=post,
    )

    assert asyncio.run(chat.acomplete([{"role": "user", "content": "hi"}])) == "ok"


def test_async_retrieve_reads_what_sync_ingest_stored() -> None:
    rag = build_memory_rag()
    rag.ingest_text(tenant_id="tenant_a", title="手册", text="员工年假有十天")
    result = asyncio.run(rag.retrieve(RetrieveRequest(query="年假", tenant_id="tenant_a")))
    assert result.cache_hit is False
    assert any("年假" in item.text for item in result.citations)


class _Reply:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}
