"""Idempotent side effects.

A checkpoint only records that a node finished. The crash window is the gap
after an external call returns and before that checkpoint commits. The action
key closes that gap: it is committed before the call, and the receipt is
committed before the checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EffectKind(StrEnum):
    TIMEOUT = "timeout"
    SERVER = "server"
    CLIENT = "client"
    AUTH = "auth"
    NO_RECEIPT = "no_receipt"


class ClaimState(StrEnum):
    PROCEED = "proceed"
    COMPLETED = "completed"
    BUSY = "busy"
    UNKNOWN = "unknown"


class EffectError(Exception):
    """A skill call failed in a way the retry policy understands."""

    def __init__(self, message: str, *, kind: EffectKind) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class ClaimResult:
    state: ClaimState
    result_ref: str | None = None
    holder_run_id: str | None = None


@dataclass(slots=True)
class EffectOutcome:
    disposition: str
    result_ref: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class IdempotencyStore(Protocol):
    """Durable action keys. Each method commits before it returns."""

    def claim(self, *, key: str, tenant_id: str, run_id: str, action_type: str) -> ClaimResult: ...

    def complete(self, *, key: str, tenant_id: str, result_ref: str) -> None: ...

    def mark_unknown(self, *, key: str, tenant_id: str) -> None: ...


class MemoryIdempotencyStore:
    """In-process store for the retry policy. PostgreSQL is the real ledger."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, str | None]] = {}

    def claim(self, *, key: str, tenant_id: str, run_id: str, action_type: str) -> ClaimResult:
        slot = (tenant_id, key)
        row = self.rows.get(slot)
        if row is None:
            self.rows[slot] = {
                "status": "in_progress",
                "run_id": run_id,
                "action_type": action_type,
                "result_ref": None,
            }
            return ClaimResult(state=ClaimState.PROCEED, holder_run_id=run_id)
        return _from_status(row["status"], row["result_ref"], row["run_id"])

    def complete(self, *, key: str, tenant_id: str, result_ref: str) -> None:
        row = self.rows[(tenant_id, key)]
        if row["status"] != "in_progress":
            return
        row["status"] = "completed"
        row["result_ref"] = result_ref

    def mark_unknown(self, *, key: str, tenant_id: str) -> None:
        row = self.rows[(tenant_id, key)]
        if row["status"] != "in_progress":
            return
        row["status"] = "unknown"


def _from_status(status: str | None, result_ref: str | None, holder_run_id: str | None) -> ClaimResult:
    if status == "completed":
        return ClaimResult(state=ClaimState.COMPLETED, result_ref=result_ref, holder_run_id=holder_run_id)
    if status == "unknown":
        return ClaimResult(state=ClaimState.UNKNOWN, result_ref=result_ref, holder_run_id=holder_run_id)
    return ClaimResult(state=ClaimState.BUSY, holder_run_id=holder_run_id)
