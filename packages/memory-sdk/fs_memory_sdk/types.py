"""Minimal memory types (session / working / long-term)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MemoryScope(StrEnum):
    SESSION = "session"
    WORKING = "working"
    LONG_TERM = "long_term"


class MemoryRecord(BaseModel):
    scope: MemoryScope
    key: str
    content: dict[str, Any] = Field(default_factory=dict)
    token_estimate: int = 0
    tenant_id: str = ""
    user_id: str = ""
