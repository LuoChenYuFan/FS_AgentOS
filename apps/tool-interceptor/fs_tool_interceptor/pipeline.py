"""L1-first interceptor scaffold. L2/L3 are stubs."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fs_skill_sdk import SkillSpec


class InterceptorDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(slots=True)
class InterceptorResult:
    decision: InterceptorDecision
    reason: str
    layer: str


class ToolInterceptor:
    """L1 allow-list. Skills are denied unless this call names them, when default_deny is on."""

    def __init__(self, *, default_deny: bool = False) -> None:
        self._default_deny = default_deny

    def check_before_call(
        self,
        spec: SkillSpec,
        arguments: dict[str, Any],
        *,
        allowed: Collection[str] | None = None,
    ) -> InterceptorResult:
        del arguments
        if spec.requires_approval:
            return InterceptorResult(
                decision=InterceptorDecision.ESCALATE,
                reason="requires_approval",
                layer="L1",
            )
        if allowed is not None and spec.name not in allowed:
            return InterceptorResult(
                decision=InterceptorDecision.DENY,
                reason="skill_not_allowed",
                layer="L1",
            )
        if allowed is None and self._default_deny:
            return InterceptorResult(
                decision=InterceptorDecision.DENY,
                reason="skill_not_allowed",
                layer="L1",
            )
        return InterceptorResult(
            decision=InterceptorDecision.ALLOW,
            reason="l1_pass",
            layer="L1",
        )

    def check_after_call(self, spec: SkillSpec, raw_result: Any) -> InterceptorResult:
        # L2 stub: passthrough with flag (fail-open for low risk in scaffold).
        _ = (spec, raw_result)
        return InterceptorResult(
            decision=InterceptorDecision.ALLOW,
            reason="l2_stub_passthrough",
            layer="L2",
        )
