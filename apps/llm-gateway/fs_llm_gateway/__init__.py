"""LLM gateway stub (Portkey/Helicone/self-hosted later)."""

from fs_common.errors import ModelQuotaExhausted
from fs_llm_gateway.client import LlmGatewayClient
from fs_llm_gateway.dashscope import (
    DashScopeChat,
    chat_for_tier,
    dashscope_chat_from_env,
)

__all__ = [
    "DashScopeChat",
    "LlmGatewayClient",
    "ModelQuotaExhausted",
    "chat_for_tier",
    "dashscope_chat_from_env",
]
