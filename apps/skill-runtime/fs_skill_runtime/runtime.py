"""Skill execution behind the interceptor.

`invoke` is the unapproved door: a skill that requires approval stops there.
`apply` runs only after approval, and it is the path that talks to the action key.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fs_common import NotImplementedYet
from fs_skill_sdk import SkillSpec
from fs_tool_interceptor import InterceptorDecision, ToolInterceptor

from fs_skill_runtime.effects import (
    ClaimState,
    EffectError,
    EffectKind,
    EffectOutcome,
    IdempotencyStore,
)

_RETRY_LIMIT = 3
Handler = Callable[[dict[str, Any]], str]


class SkillRuntime:
    def __init__(
        self,
        interceptor: ToolInterceptor | None = None,
        store: IdempotencyStore | None = None,
    ) -> None:
        self._interceptor = interceptor or ToolInterceptor()
        self._store = store
        self._registry: dict[str, SkillSpec] = {}
        self._handlers: dict[str, Handler] = {}

    def register(self, spec: SkillSpec) -> None:
        self._registry[spec.name] = spec

    def register_handler(self, name: str, handler: Handler) -> None:
        self._handlers[name] = handler

    def registered(self, name: str) -> bool:
        return name in self._registry

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
        *,
        allowed: Collection[str] | None = None,
    ) -> Any:
        """Run one read skill after the interceptor, the schema check, and the timeout."""

        spec = self._registry.get(name)
        if spec is None:
            raise ValueError(f"skill {name} is not registered")
        before = self._interceptor.check_before_call(spec, arguments, allowed=allowed)
        if before.decision != InterceptorDecision.ALLOW:
            raise ValueError(f"skill {name} blocked: {before.reason}")
        problems = _argument_problems(spec.input_schema, arguments)
        if problems:
            raise ValueError(f"skill {name} arguments rejected: {', '.join(problems)}")
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(handler, arguments)
        try:
            result = future.result(timeout=spec.timeout_sec)
        except TimeoutError as exc:
            raise ValueError(f"skill {name} timed out") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        after = self._interceptor.check_after_call(spec, result)
        if after.decision == InterceptorDecision.DENY:
            raise ValueError(f"skill {name} result blocked: {after.reason}")
        return result

    async def invoke(self, name: str, arguments: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        spec = self._registry.get(name)
        if spec is None:
            raise NotImplementedYet(f"skill:{name}")
        if spec.idempotency_required and not idempotency_key:
            raise ValueError("idempotency_key required")

        before = self._interceptor.check_before_call(spec, arguments)
        if before.decision != InterceptorDecision.ALLOW:
            return {"status": before.decision.value, "reason": before.reason, "layer": before.layer}

        raise NotImplementedYet(f"skill_connector:{name}")

    def apply(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
        tenant_id: str,
        run_id: str,
        approved: bool = False,
    ) -> EffectOutcome:
        spec = self._registry.get(name)
        if spec is None:
            raise NotImplementedYet(f"skill:{name}")
        if spec.requires_approval and not approved:
            return EffectOutcome(disposition="escalate", error_code="requires_approval")
        if spec.idempotency_required and not idempotency_key:
            raise ValueError("idempotency_key required")
        if name not in self._handlers:
            raise NotImplementedYet(f"skill_connector:{name}")

        if spec.idempotency_required:
            if self._store is None:
                raise ValueError("idempotency store required")
            claim = self._store.claim(
                key=idempotency_key,
                tenant_id=tenant_id,
                run_id=run_id,
                action_type=name,
            )
            if claim.state is ClaimState.COMPLETED:
                return EffectOutcome(disposition="replayed", result_ref=claim.result_ref)
            if claim.state is ClaimState.UNKNOWN:
                return EffectOutcome(disposition="unknown", error_code="effect_unknown")
            if claim.state is ClaimState.BUSY:
                if claim.holder_run_id == run_id:
                    self._store.mark_unknown(key=idempotency_key, tenant_id=tenant_id)
                    return EffectOutcome(disposition="unknown", error_code="effect_unknown")
                return EffectOutcome(disposition="conflict", error_code="in_progress")

        try:
            receipt = self._call(spec, arguments)
        except EffectError as exc:
            return self._fail(spec, idempotency_key, tenant_id, exc)
        except NotImplementedYet:
            raise
        except Exception as exc:
            if spec.idempotency_required:
                self._store.mark_unknown(key=idempotency_key, tenant_id=tenant_id)
                return EffectOutcome(disposition="unknown", error_code="no_receipt", error_message=str(exc))
            raise

        if spec.idempotency_required:
            self._store.complete(key=idempotency_key, tenant_id=tenant_id, result_ref=receipt)
        return EffectOutcome(disposition="sent", result_ref=receipt)

    def _fail(self, spec: SkillSpec, idempotency_key: str, tenant_id: str, exc: EffectError) -> EffectOutcome:
        if spec.idempotency_required and exc.kind in {EffectKind.TIMEOUT, EffectKind.NO_RECEIPT}:
            self._store.mark_unknown(key=idempotency_key, tenant_id=tenant_id)
            return EffectOutcome(disposition="unknown", error_code=exc.kind.value, error_message=str(exc))
        return EffectOutcome(disposition="failed", error_code=exc.kind.value, error_message=str(exc))

    def _call(self, spec: SkillSpec, arguments: dict[str, Any]) -> str:
        handler = self._handlers[spec.name]
        attempts = _RETRY_LIMIT if spec.safe_retry else 1
        for attempt in range(attempts):
            try:
                receipt = handler(arguments)
            except EffectError as exc:
                retryable = exc.kind in {EffectKind.TIMEOUT, EffectKind.SERVER} and spec.safe_retry
                if retryable and attempt + 1 < attempts:
                    continue
                raise
            if receipt is None or str(receipt) == "":
                raise EffectError("side effect returned no receipt", kind=EffectKind.NO_RECEIPT)
            return str(receipt)
        raise EffectError("side effect failed", kind=EffectKind.SERVER)


def _argument_problems(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for key, expected in schema.items():
        if key not in arguments:
            continue
        if not _value_matches(str(expected), arguments[key]):
            problems.append(f"{key} should be {expected}")
    return problems


def _value_matches(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        if isinstance(value, bool):
            return False
        if isinstance(value, int | float):
            return True
        if isinstance(value, str):
            try:
                float(value)
            except ValueError:
                return False
            return True
        return False
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True
