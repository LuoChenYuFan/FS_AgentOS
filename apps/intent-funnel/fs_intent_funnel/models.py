"""Model tiers return a structured guess. A low confidence is a failed layer."""

from __future__ import annotations

from typing import Literal, Protocol

from fs_common.contracts.intent import INTENT_UNKNOWN
from fs_common.jsontext import loads_json_object
from pydantic import BaseModel, Field, ValidationError

from fs_intent_funnel.config import FunnelConfig


class IntentGuess(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    second_intent: str | None = None
    failure_reason: str = ""


class IntentModel(Protocol):
    def classify(self, text: str) -> IntentGuess: ...


_INTENTS = frozenset(
    {
        "knowledge",
        "policy_lookup",
        "weather",
        "hotel",
        "itinerary",
        "send_email",
        "task",
        "chat",
        "unknown",
    }
)
_INTENT_CHOICES = (
    "knowledge、policy_lookup、weather、hotel、itinerary、send_email、task、chat"
)

_INTENT_NAMES = Literal[
    "knowledge",
    "policy_lookup",
    "weather",
    "hotel",
    "itinerary",
    "send_email",
    "task",
    "chat",
    "unknown",
]


class IntentSubmission(BaseModel):
    """Schema sent to the model. The class is the format; JSON Schema is generated from it."""

    intent: _INTENT_NAMES = Field(description="这次请求的主意图")
    confidence: float = Field(ge=0.0, le=1.0, description="把握，0到1")
    second_intent: _INTENT_NAMES | None = Field(
        default=None, description="同时存在的第二意图；没有则为空"
    )


_INTENT_SYSTEM = (
    "你是意图分类器。不要回答用户的问题。"
    "调用 submit_intent，按参数说明填写分类结果。"
    "整句只是查阅制度、手册、知识库里的资料时用 knowledge 或 policy_lookup。"
    "从一地到另一地、问地铁公交换乘、怎么走、要多久，用 itinerary，不要用 knowledge。"
    "闲聊、问你是谁、问你能做什么或有哪些技能，用 chat，不要用 knowledge。"
    "查资料的同时还要办事，intent 用 task，second_intent 用 knowledge。"
    "一句话里有多件要办的事，intent 用 task。"
)


class TextChat(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


class _ReadError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


class StructuredIntentModel:
    """One chat model. A low or unreadable answer fails this layer."""

    def __init__(self, chat: TextChat, *, minimum: float | None = None) -> None:
        self._chat = chat
        self._minimum = (
            FunnelConfig().large_llm_min_confidence if minimum is None else minimum
        )
        self.last_tries: list[str] = []

    def classify(self, text: str) -> IntentGuess:
        feedback = ""
        submit = getattr(self._chat, "complete_structured", None)
        failed = IntentGuess(
            intent=INTENT_UNKNOWN, confidence=0.0, failure_reason="parse_error"
        )
        tries: list[str] = []
        for _ in range(2):
            content = f"{feedback}\n{text}".strip() if feedback else text
            messages = [
                {"role": "system", "content": _INTENT_SYSTEM},
                {"role": "user", "content": content},
            ]
            try:
                guess = self._read(messages, submit)
            except _ReadError as exc:
                feedback = exc.detail
                tries.append(exc.reason)
                failed = IntentGuess(
                    intent=INTENT_UNKNOWN,
                    confidence=0.0,
                    failure_reason=exc.reason,
                )
                continue
            except RuntimeError as exc:
                feedback = str(exc)
                tries.append("model_error")
                failed = IntentGuess(
                    intent=INTENT_UNKNOWN,
                    confidence=0.0,
                    failure_reason="model_error",
                )
                continue
            reason = _business_reason(guess, self._minimum)
            if reason:
                feedback = f"confidence {_shown(guess.confidence)} 低于 {_shown(self._minimum)}"
                tries.append(reason)
                failed = guess.model_copy(update={"failure_reason": reason})
                continue
            tries.append("")
            self.last_tries = tries
            return guess
        self.last_tries = tries
        return failed

    async def aclassify(self, text: str) -> IntentGuess:
        feedback = ""
        failed = IntentGuess(
            intent=INTENT_UNKNOWN, confidence=0.0, failure_reason="parse_error"
        )
        tries: list[str] = []
        for _ in range(2):
            content = f"{feedback}\n{text}".strip() if feedback else text
            messages = [
                {"role": "system", "content": _INTENT_SYSTEM},
                {"role": "user", "content": content},
            ]
            try:
                guess = await self._aread(messages)
            except _ReadError as exc:
                feedback = exc.detail
                tries.append(exc.reason)
                failed = IntentGuess(
                    intent=INTENT_UNKNOWN,
                    confidence=0.0,
                    failure_reason=exc.reason,
                )
                continue
            except RuntimeError as exc:
                feedback = str(exc)
                tries.append("model_error")
                failed = IntentGuess(
                    intent=INTENT_UNKNOWN,
                    confidence=0.0,
                    failure_reason="model_error",
                )
                continue
            reason = _business_reason(guess, self._minimum)
            if reason:
                feedback = f"confidence {_shown(guess.confidence)} 低于 {_shown(self._minimum)}"
                tries.append(reason)
                failed = guess.model_copy(update={"failure_reason": reason})
                continue
            tries.append("")
            self.last_tries = tries
            return guess
        self.last_tries = tries
        return failed

    def _read(self, messages: list[dict[str, str]], submit: object) -> IntentGuess:
        try:
            if callable(submit):
                payload = submit(messages, IntentSubmission)
                if not isinstance(payload, dict):
                    raise TypeError("返回的不是 JSON 对象")
            else:
                payload = _json_object(self._chat.complete(messages))
            checked = IntentSubmission.model_validate(_prepare(payload))
        except ValidationError as exc:
            raise _ReadError("schema_error", _schema_feedback(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise _ReadError("parse_error", str(exc)) from exc
        return _guess(checked.model_dump())

    async def _aread(self, messages: list[dict[str, str]]) -> IntentGuess:
        try:
            payload = await _intent_payload(self._chat, messages)
            checked = IntentSubmission.model_validate(_prepare(payload))
        except ValidationError as exc:
            raise _ReadError("schema_error", _schema_feedback(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise _ReadError("parse_error", str(exc)) from exc
        return _guess(checked.model_dump())


async def _intent_payload(chat: object, messages: list[dict[str, str]]) -> dict[str, object]:
    structured = getattr(chat, "acomplete_structured", None)
    if callable(structured):
        payload = await structured(messages, IntentSubmission)
    else:
        complete = getattr(chat, "acomplete", None)
        if not callable(complete):
            sync_structured = getattr(chat, "complete_structured", None)
            if callable(sync_structured):
                payload = sync_structured(messages, IntentSubmission)
            else:
                payload = _json_object(chat.complete(messages))  # type: ignore[attr-defined]
        else:
            payload = _json_object(await complete(messages))
    if not isinstance(payload, dict):
        raise TypeError("返回的不是 JSON 对象")
    return payload


class BelowThresholdModel:
    """Local stand-in. A real model is passed in; this one never clears the bar."""

    def __init__(self) -> None:
        self.calls = 0

    def classify(self, text: str) -> IntentGuess:
        del text
        self.calls += 1
        return IntentGuess(intent=INTENT_UNKNOWN, confidence=0.0)


def _prepare(payload: dict[str, object]) -> dict[str, object]:
    data = dict(payload)
    intent = _label_for_schema(data.get("intent"))
    data["intent"] = intent
    data["second_intent"] = _second_for_schema(data.get("second_intent"), intent)
    return data


def _label_for_schema(value: object) -> object:
    if not isinstance(value, str) or not value.strip():
        return value
    return _canon_intent(value) or value


def _second_for_schema(value: object, intent: object) -> object:
    if not isinstance(value, str) or not value.strip():
        return None
    canon = _canon_intent(value)
    if canon is None:
        return value
    if canon == intent:
        return None
    return canon


def _shown(value: float) -> str:
    return format(value, "g")


def _schema_feedback(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "字段不符合规定"
    item = errors[0]
    field = ".".join(str(part) for part in item.get("loc", ()))
    if field == "confidence":
        return f"confidence {item.get('input')} 超出 0 到 1"
    if field in {"intent", "second_intent"}:
        return f"{field} 不在规定取值里，只能是 {_INTENT_CHOICES}"
    if field:
        return f"{field} 不符合规定"
    return "字段不符合规定"


def _business_reason(guess: IntentGuess, minimum: float) -> str:
    """A value the schema allows and the business still refuses."""
    if guess.intent == INTENT_UNKNOWN:
        return ""
    if guess.confidence < minimum:
        return "business_confidence"
    return ""


def _guess(payload: dict[str, object]) -> IntentGuess:
    intent = _canon_intent(payload.get("intent"))
    if intent is None:
        intent = INTENT_UNKNOWN
    second = _canon_intent(payload.get("second_intent"))
    if second == intent:
        second = None
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    if intent == INTENT_UNKNOWN:
        confidence = 0.0
    return IntentGuess(intent=intent, confidence=confidence, second_intent=second)


_INTENT_ALIASES = {
    "出行规划": "task",
    "任务": "task",
    "办事": "task",
    "天气": "weather",
    "查天气": "weather",
    "天气查询": "weather",
    "酒店": "hotel",
    "查酒店": "hotel",
    "行程": "itinerary",
    "行程规划": "itinerary",
    "发邮件": "send_email",
    "邮件": "send_email",
    "知识": "knowledge",
    "知识库": "knowledge",
    "制度": "policy_lookup",
    "查制度": "policy_lookup",
}


def _canon_intent(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    if name in _INTENTS:
        return name
    return _INTENT_ALIASES.get(name)


def _json_object(raw: str) -> dict[str, object]:
    return loads_json_object(raw)
