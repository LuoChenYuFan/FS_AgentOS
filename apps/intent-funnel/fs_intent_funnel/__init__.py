"""Intent funnel between the gateway and the orchestrator."""

from fs_intent_funnel.config import FunnelConfig
from fs_intent_funnel.funnel import (
    FunnelResult,
    IntentFunnel,
    LayerAttempt,
    default_funnel,
    unknown_user_message,
)
from fs_intent_funnel.handoff import AcceptKnowledge, KnowledgeHandoff
from fs_intent_funnel.log import DecisionLog, MemoryDecisionLog, PostgresDecisionLog
from fs_intent_funnel.models import (
    BelowThresholdModel,
    IntentGuess,
    IntentModel,
    StructuredIntentModel,
)
from fs_intent_funnel.promote import promote_repeated, rule_candidates
from fs_intent_funnel.vectors import IntentExampleIndex, IntentHit, MemoryIntentIndex

__all__ = [
    "AcceptKnowledge",
    "BelowThresholdModel",
    "DecisionLog",
    "FunnelConfig",
    "FunnelResult",
    "IntentExampleIndex",
    "IntentFunnel",
    "IntentGuess",
    "IntentHit",
    "IntentModel",
    "KnowledgeHandoff",
    "LayerAttempt",
    "MemoryDecisionLog",
    "MemoryIntentIndex",
    "PostgresDecisionLog",
    "StructuredIntentModel",
    "default_funnel",
    "promote_repeated",
    "rule_candidates",
    "unknown_user_message",
]
