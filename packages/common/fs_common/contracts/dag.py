"""Task DAG, the contract between Planner and the scheduler.

`deps` controls order, `output_key` names a result, `condition` decides whether
a task runs. Thresholds live in `condition.value`; the scheduler only implements `op`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ConditionOp(StrEnum):
    LT = "<"
    GT = ">"
    EQ = "=="
    IN = "in"


class TaskCondition(BaseModel):
    field: str
    op: ConditionOp
    value: Any


class TaskNode(BaseModel):
    """One node on the plan. `args` may contain ``${{output_key.field}}`` placeholders."""

    id: int
    agent: str
    args: dict[str, Any] = Field(default_factory=dict)
    deps: list[int] = Field(default_factory=list)
    output_key: str | None = None
    condition: TaskCondition | None = None


class TaskDag(BaseModel):
    run_id: str
    tasks: list[TaskNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def tasks_refer_to_known_ids(self) -> TaskDag:
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        known = set(ids)
        for task in self.tasks:
            missing = [dep for dep in task.deps if dep not in known]
            if missing:
                raise ValueError(f"task {task.id} depends on unknown task ids: {missing}")
        return self
