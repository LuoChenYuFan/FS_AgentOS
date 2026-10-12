"""Layer 0 contracts: shared shapes for identity, runs, intent, and checkpoints.

这些模型只规定字段。断点续跑、鉴权和调度不在这一层执行。
"""

from fs_common.contracts.dag import ConditionOp, TaskCondition, TaskDag, TaskNode
from fs_common.contracts.intent import (
    INTENT_UNKNOWN,
    IntentDecision,
    IntentDecisionRecord,
    IntentLayer,
    IntentRoute,
)
from fs_common.contracts.principal import AuthMethod, Principal
from fs_common.contracts.records import (
    ApprovalRecord,
    ApprovalStatus,
    ArtifactRecord,
    AuditRecord,
    IdempotencyRecord,
    IdempotencyStatus,
    RunRecord,
    RunStatus,
    SessionRecord,
    TaskRecord,
    TaskStatus,
)
from fs_common.contracts.state import (
    BudgetUsed,
    CheckpointRecord,
    PendingApproval,
    RuntimeState,
)

__all__ = [
    "INTENT_UNKNOWN",
    "ApprovalRecord",
    "ApprovalStatus",
    "ArtifactRecord",
    "AuditRecord",
    "AuthMethod",
    "BudgetUsed",
    "CheckpointRecord",
    "ConditionOp",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "IntentDecision",
    "IntentDecisionRecord",
    "IntentLayer",
    "IntentRoute",
    "PendingApproval",
    "Principal",
    "RunRecord",
    "RunStatus",
    "RuntimeState",
    "SessionRecord",
    "TaskCondition",
    "TaskDag",
    "TaskNode",
    "TaskRecord",
    "TaskStatus",
]
