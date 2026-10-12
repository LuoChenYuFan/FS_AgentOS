"""Row shapes for the layer-0 tables. Writing these rows comes in a later layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from fs_common.contracts.dag import TaskCondition, TaskDag
from fs_common.contracts.state import RuntimeState


class RunStatus(StrEnum):
    RUNNING = "running"
    SUSPENDED = "suspended"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class SessionRecord(BaseModel):
    id: str
    user_id: str
    tenant_id: str
    channel: str
    status: str


class RunRecord(BaseModel):
    """One user request. `idempotency_key` stops the same request opening two runs."""

    id: str
    session_id: str
    tenant_id: str
    user_input: str
    idempotency_key: str
    status: RunStatus = RunStatus.RUNNING
    current_node: str | None = None
    replan_count: int = 0
    budget_token_used: int = 0
    budget_cost_used: float = 0
    plan: TaskDag | None = None
    state: RuntimeState | None = None


class TaskRecord(BaseModel):
    id: str
    run_id: str
    task_index: int
    agent_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    deps: list[int] = Field(default_factory=list)
    output_key: str | None = None
    condition: TaskCondition | None = None
    status: TaskStatus = TaskStatus.PENDING
    condition_result: bool | None = None
    retry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None


class ArtifactRecord(BaseModel):
    """Small results stay in `payload`. Large ones keep an OSS pointer in `payload_ref`."""

    id: str
    run_id: str
    task_id: str
    output_key: str
    payload: dict[str, Any] | None = None
    payload_ref: str | None = None
    token_size: int = 0

    @model_validator(mode="after")
    def payload_or_pointer(self) -> ArtifactRecord:
        if self.payload is None and not self.payload_ref:
            raise ValueError("artifact needs payload or payload_ref")
        return self


class ApprovalRecord(BaseModel):
    id: str
    run_id: str
    task_id: str
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus
    idempotency_key: str


class IdempotencyRecord(BaseModel):
    """Action-level key. Separate from the run entry key, which only dedupes opening a run."""

    key: str
    run_id: str
    tenant_id: str
    action_type: str
    status: IdempotencyStatus
    result_ref: str | None = None


class AuditRecord(BaseModel):
    id: str
    run_id: str
    actor: str
    action: str
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    citations: list[Any] = Field(default_factory=list)
