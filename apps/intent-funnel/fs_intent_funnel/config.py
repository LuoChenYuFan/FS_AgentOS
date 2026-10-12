"""Pass conditions live here. They are not written into a prompt."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FunnelConfig(BaseModel):
    rules_min_prob: float = Field(default=0.75, ge=0.0, le=1.0)
    rules_margin: float = Field(default=0.2, ge=0.0, le=1.0)
    vector_min_score: float = Field(default=0.82, ge=0.0, le=1.0)
    vector_margin: float = Field(default=0.08, ge=0.0, le=1.0)
    small_llm_min_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    large_llm_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    knowledge_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    skip_small_llm: bool = False


KNOWLEDGE_INTENTS = frozenset({"policy_lookup", "knowledge"})
