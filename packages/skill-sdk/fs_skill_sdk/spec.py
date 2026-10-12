"""Skill Spec — schema contract for tools/MCP-compatible skills."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SkillRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DeliverySemantics(StrEnum):
    AT_LEAST_ONCE = "at_least_once"
    AT_MOST_ONCE = "at_most_once"
    EXACTLY_ONCE_EFFECT = "exactly_once_effect"


class SkillSpec(BaseModel):
    name: str
    description: str = ""
    risk: SkillRisk = SkillRisk.LOW
    requires_approval: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_sec: int = 30
    idempotency_required: bool = False
    safe_retry: bool = True
    delivery_semantics: DeliverySemantics = DeliverySemantics.AT_LEAST_ONCE
    audit: str = "full"
