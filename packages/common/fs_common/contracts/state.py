"""Runtime ledger for one run, and the snapshot stored at each node boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from fs_common.contracts.dag import TaskDag


class BudgetUsed(BaseModel):
    token: int = 0
    cost: float = 0


class PendingApproval(BaseModel):
    """Irreversible work waiting for a person. Nothing has been sent yet."""

    task_id: int
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(BaseModel):
    """Orchestrator ledger. Agents only receive the slice the scheduler injects.

    Tool payloads stay in `task_results`, not in `messages`.
    """

    messages: list[dict[str, Any]] = Field(default_factory=list)
    plan: TaskDag | None = None
    task_results: dict[str, Any] = Field(default_factory=dict)
    conditions_met: dict[str, bool] = Field(default_factory=dict)
    pending_approval: PendingApproval | None = None
    replan_count: int = 0
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)


class CheckpointRecord(BaseModel):
    """One saved copy of RuntimeState after a node finishes."""

    id: str
    run_id: str
    node_name: str
    step_seq: int = Field(ge=0)
    state_snapshot: RuntimeState
