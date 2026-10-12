"""Chat completions on the Aliyun compatible API."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
from fs_common.errors import ModelQuotaExhausted
from fs_common.jsontext import loads_json_object
from fs_common.model_gate import model_gate
from pydantic import BaseModel

_TIMEOUT_SECONDS = 60.0


class DashScopeChat:
    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        model: str,
        timeout: float = _TIMEOUT_SECONDS,
        post: Callable[..., httpx.Response] | None = None,
        apost: Callable[..., object] | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._url = f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
        self._post = post or httpx.post
        self._apost = apost

    def complete(
        self, messages: list[dict[str, str]], *, enable_thinking: bool | None = None
    ) -> str:
        return _message_text(self._exchange(_chat_body(self.model, messages, enable_thinking)))

    async def acomplete(
        self, messages: list[dict[str, str]], *, enable_thinking: bool | None = None
    ) -> str:
        body = _chat_body(self.model, messages, enable_thinking)
        return _message_text(await self._aexchange(body))

    def complete_structured(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> dict[str, Any]:
        """Force a function call whose parameters are the Pydantic model's JSON Schema."""

        return _function_arguments(self._exchange(_tool_body(self.model, messages, schema)))

    async def acomplete_structured(
        self, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        body = _tool_body(self.model, messages, schema)
        return _function_arguments(await self._aexchange(body))

    def _exchange(self, body: dict[str, Any]) -> object:
        with model_gate().blocking():
            response = self._post(
                self._url,
                headers=_headers(self._api_key),
                json=body,
                timeout=self._timeout,
            )
            return _read_response(response, model=self.model)

    async def _aexchange(self, body: dict[str, Any]) -> object:
        async with model_gate().slot():
            if self._apost is not None:
                response = await self._apost(
                    self._url,
                    headers=_headers(self._api_key),
                    json=body,
                    timeout=self._timeout,
                )
                return _read_response(response, model=self.model)
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, headers=_headers(self._api_key), json=body)
                return _read_response(response, model=self.model)


def _chat_body(
    model: str, messages: list[dict[str, str]], enable_thinking: bool | None
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": messages}
    if enable_thinking is not None:
        body["enable_thinking"] = enable_thinking
    return body


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _tool_body(model: str, messages: list[dict[str, str]], schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "enable_thinking": False,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "submit_intent",
                    "description": schema.__doc__ or schema.__name__,
                    "parameters": schema.model_json_schema(),
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "submit_intent"}},
    }


def _read_response(response: object, *, model: str) -> object:
    try:
        raise_for_status = getattr(response, "raise_for_status", None)
        as_json = getattr(response, "json", None)
        if not callable(raise_for_status) or not callable(as_json):
            raise TypeError("chat response was not an HTTP response")
        raise_for_status()
        return as_json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        if _quota_exhausted(detail):
            raise ModelQuotaExhausted(model) from exc
        raise RuntimeError(f"chat request failed: {exc.response.status_code} {detail[:300]}") from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        if str(exc).startswith("chat request failed"):
            raise
        raise RuntimeError("chat request failed") from exc


def _quota_exhausted(detail: str) -> bool:
    lowered = detail.lower()
    return "insufficient_quota" in lowered or "free quota exhausted" in lowered


def chat_for_tier(tier: str) -> DashScopeChat | None:
    """Cheap models plan and classify. Strong models are for heavier agents."""

    if tier == "strong":
        return dashscope_chat_from_env("STRONG_MODEL") or dashscope_chat_from_env("CHAT_MODEL")
    return (
        dashscope_chat_from_env("CHEAP_MODEL")
        or dashscope_chat_from_env("INTENT_MODEL")
        or dashscope_chat_from_env("CHAT_MODEL")
    )


def dashscope_chat_from_env(model_var: str) -> DashScopeChat | None:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    workspace_id = os.environ.get("WORKSPACE_ID", "")
    model = os.environ.get(model_var, "")
    if not api_key or not workspace_id or not model:
        return None
    return DashScopeChat(api_key=api_key, workspace_id=workspace_id, model=model)


def _message_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise TypeError("chat response was not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        output = payload.get("output")
        if isinstance(output, dict):
            choices = output.get("choices")
            if isinstance(output.get("text"), str):
                return output["text"]
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("chat response did not include a message")
    message = choices[0].get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(choices[0].get("text"), str):
        return choices[0]["text"]
    raise RuntimeError("chat response did not include a message")


def _function_arguments(payload: object) -> dict[str, Any]:
    message = _assistant_message(payload)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls and isinstance(tool_calls[0], dict):
        function = tool_calls[0].get("function")
        if isinstance(function, dict):
            return _object_from_arguments(function.get("arguments"))
    content = message.get("content")
    if isinstance(content, str):
        return _object_from_text(content)
    raise ValueError("model did not call the schema function")


def _assistant_message(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("chat response was not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("chat response did not include a message")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise TypeError("chat response did not include a message")
    return message


def _object_from_arguments(arguments: object) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        return _object_from_text(arguments)
    raise TypeError("tool arguments were not an object")


def _object_from_text(raw: str) -> dict[str, Any]:
    return loads_json_object(raw)
