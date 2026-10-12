"""Intent-funnel result. `unknown` is a finished outcome, not a failure to fill in."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

INTENT_UNKNOWN = "unknown"
FailureReason = Literal[
    "",
    "parse_error",
    "schema_error",
    "business_confidence",
    "model_error",
]


class IntentRoute(StrEnum):
    KNOWLEDGE = "knowledge"
    ORCHESTRATE = "orchestrate"
    UNKNOWN = "unknown"


class IntentLayer(StrEnum):
    REGEX = "regex"
    RULES = "rules"
    VECTOR = "vector"
    SMALL_LLM = "small_llm"
    LARGE_LLM = "large_llm"
    NONE = "none"


class IntentDecision(BaseModel):
    """One pass/fail judgement from the funnel."""

    intent: str
    route: IntentRoute
    confidence: float = Field(ge=0.0, le=1.0)
    layer_hit: IntentLayer
    passed: bool
    failure_reason: FailureReason = ""

    @model_validator(mode="after")
    def unknown_does_not_pass(self) -> IntentDecision:
        if self.route is IntentRoute.UNKNOWN:
            if self.intent != INTENT_UNKNOWN or self.passed:
                raise ValueError(
                    "unknown route requires intent 'unknown' and passed=False"
                )
        elif self.passed and self.intent == INTENT_UNKNOWN:
            raise ValueError("a passed decision cannot use intent 'unknown'")
        return self


class IntentDecisionRecord(IntentDecision):
    """Stored copy of a decision, including text used later to promote new rules."""

    id: str
    tenant_id: str
    raw_text: str
    run_id: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    false_positive: bool | None = None
    attempt_reasons: list[dict[str, str]] = Field(default_factory=list)
