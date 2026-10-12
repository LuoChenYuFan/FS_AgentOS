"""Walk the funnel. The first layer that clears its own bar wins."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fs_common import FsError
from fs_common.contracts.intent import (
    INTENT_UNKNOWN,
    IntentDecision,
    IntentLayer,
    IntentRoute,
)

from fs_intent_funnel.config import KNOWLEDGE_INTENTS, FunnelConfig
from fs_intent_funnel.models import BelowThresholdModel, IntentGuess, IntentModel
from fs_intent_funnel.regexes import policy_lookup
from fs_intent_funnel.rules import rule_scores
from fs_intent_funnel.scoring import clears_margin
from fs_intent_funnel.vectors import IntentExampleIndex, MemoryIntentIndex


@dataclass(slots=True)
class LayerAttempt:
    layer: IntentLayer
    passed: bool
    score: float
    latency_ms: int
    intent: str | None = None
    second_intent: str | None = None
    failure_reason: str = ""
    candidate: str | None = None


@dataclass(slots=True)
class FunnelResult:
    decision: IntentDecision
    attempts: list[LayerAttempt] = field(default_factory=list)
    latency_ms: int = 0


class IntentFunnel:
    def __init__(
        self,
        *,
        config: FunnelConfig | None = None,
        index: IntentExampleIndex | None = None,
        small_model: IntentModel | None = None,
        large_model: IntentModel | None = None,
    ) -> None:
        self.config = config or FunnelConfig()
        self.index = index or MemoryIntentIndex()
        self.small_model = small_model or BelowThresholdModel()
        self.large_model = large_model or BelowThresholdModel()

    def decide(self, text: str) -> FunnelResult:
        started = time.perf_counter()
        attempts: list[LayerAttempt] = []
        for attempt in self._layers(text):
            attempts.append(attempt)
            if attempt.passed and attempt.intent:
                return self._finish(attempt, attempts, started)
        if _last_model_failure(attempts) in _RECOGNITION_FAILURES:
            earlier = _earlier_result(attempts)
            if earlier is not None:
                return self._finish(earlier, attempts, started)
        decision = IntentDecision(
            intent=INTENT_UNKNOWN,
            route=IntentRoute.UNKNOWN,
            confidence=0.0,
            layer_hit=IntentLayer.NONE,
            passed=False,
            failure_reason=_recorded_failure(attempts, passed=False),
        )
        return FunnelResult(
            decision=decision, attempts=attempts, latency_ms=_millis(started)
        )

    async def adecide(self, text: str) -> FunnelResult:
        started = time.perf_counter()
        attempts: list[LayerAttempt] = []
        for attempt in (self._regex(text), self._rules(text), self._vector(text)):
            attempts.append(attempt)
            if attempt.passed and attempt.intent:
                return self._finish(attempt, attempts, started)
        if not self.config.skip_small_llm:
            attempt = await self._amodel(
                self.small_model,
                IntentLayer.SMALL_LLM,
                self.config.small_llm_min_confidence,
                text,
            )
            attempts.append(attempt)
            if attempt.passed and attempt.intent:
                return self._finish(attempt, attempts, started)
        attempt = await self._amodel(
            self.large_model,
            IntentLayer.LARGE_LLM,
            self.config.large_llm_min_confidence,
            text,
        )
        attempts.append(attempt)
        if attempt.passed and attempt.intent:
            return self._finish(attempt, attempts, started)
        if _last_model_failure(attempts) in _RECOGNITION_FAILURES:
            earlier = _earlier_result(attempts)
            if earlier is not None:
                return self._finish(earlier, attempts, started)
        decision = IntentDecision(
            intent=INTENT_UNKNOWN,
            route=IntentRoute.UNKNOWN,
            confidence=0.0,
            layer_hit=IntentLayer.NONE,
            passed=False,
            failure_reason=_recorded_failure(attempts, passed=False),
        )
        return FunnelResult(
            decision=decision, attempts=attempts, latency_ms=_millis(started)
        )

    def _layers(self, text: str):
        yield self._regex(text)
        yield self._rules(text)
        yield self._vector(text)
        if not self.config.skip_small_llm:
            yield self._model(
                self.small_model,
                IntentLayer.SMALL_LLM,
                self.config.small_llm_min_confidence,
                text,
            )
        yield self._model(
            self.large_model,
            IntentLayer.LARGE_LLM,
            self.config.large_llm_min_confidence,
            text,
        )

    def _regex(self, text: str) -> LayerAttempt:
        started = time.perf_counter()
        intent = policy_lookup(text)
        return LayerAttempt(
            layer=IntentLayer.REGEX,
            passed=intent is not None,
            score=1.0 if intent else 0.0,
            latency_ms=_millis(started),
            intent=intent,
        )

    def _rules(self, text: str) -> LayerAttempt:
        started = time.perf_counter()
        scores = rule_scores(text)
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        passed = clears_margin(
            [score for _, score in ordered],
            minimum=self.config.rules_min_prob,
            margin=self.config.rules_margin,
        )
        leader = ordered[0][0] if ordered else None
        return LayerAttempt(
            layer=IntentLayer.RULES,
            passed=passed,
            score=ordered[0][1] if ordered else 0.0,
            latency_ms=_millis(started),
            intent=leader if passed else None,
            candidate=leader,
        )

    def _vector(self, text: str) -> LayerAttempt:
        started = time.perf_counter()
        hits = self.index.search(text, limit=2) if text.strip() else []
        passed = clears_margin(
            [hit.score for hit in hits],
            minimum=self.config.vector_min_score,
            margin=self.config.vector_margin,
        )
        leader = hits[0] if hits else None
        return LayerAttempt(
            layer=IntentLayer.VECTOR,
            passed=passed,
            score=leader.score if leader else 0.0,
            latency_ms=_millis(started),
            intent=leader.intent if passed and leader else None,
            candidate=leader.intent if leader else None,
        )

    def _model(
        self, model: IntentModel, layer: IntentLayer, minimum: float, text: str
    ) -> LayerAttempt:
        started = time.perf_counter()
        try:
            guess = model.classify(text)
        except (FsError, OSError, RuntimeError, TypeError, ValueError):
            guess = IntentGuess(
                intent=INTENT_UNKNOWN,
                confidence=0.0,
                failure_reason="model_error",
            )
        passed = (
            guess.failure_reason == ""
            and guess.intent != INTENT_UNKNOWN
            and guess.confidence >= minimum
        )
        return LayerAttempt(
            layer=layer,
            passed=passed,
            score=guess.confidence,
            latency_ms=_millis(started),
            intent=guess.intent if passed else None,
            second_intent=guess.second_intent if passed else None,
            failure_reason=guess.failure_reason,
        )

    async def _amodel(
        self, model: IntentModel, layer: IntentLayer, minimum: float, text: str
    ) -> LayerAttempt:
        started = time.perf_counter()
        try:
            classify = getattr(model, "aclassify", None)
            if callable(classify):
                guess = await classify(text)
            else:
                guess = model.classify(text)
            if not isinstance(guess, IntentGuess):
                raise TypeError("intent model returned an unexpected value")
        except (FsError, OSError, RuntimeError, TypeError, ValueError):
            guess = IntentGuess(
                intent=INTENT_UNKNOWN,
                confidence=0.0,
                failure_reason="model_error",
            )
        passed = (
            guess.failure_reason == ""
            and guess.intent != INTENT_UNKNOWN
            and guess.confidence >= minimum
        )
        return LayerAttempt(
            layer=layer,
            passed=passed,
            score=guess.confidence,
            latency_ms=_millis(started),
            intent=guess.intent if passed else None,
            second_intent=guess.second_intent if passed else None,
            failure_reason=guess.failure_reason,
        )

    def _finish(
        self, winner: LayerAttempt, attempts: list[LayerAttempt], started: float
    ) -> FunnelResult:
        intent = winner.intent or INTENT_UNKNOWN
        route = _route(
            intent,
            winner.score,
            winner.second_intent,
            self.config.knowledge_min_confidence,
        )
        if route is IntentRoute.UNKNOWN:
            decision = IntentDecision(
                intent=INTENT_UNKNOWN,
                route=IntentRoute.UNKNOWN,
                confidence=0.0,
                layer_hit=IntentLayer.NONE,
                passed=False,
                failure_reason=_recorded_failure(attempts, passed=False),
            )
        else:
            decision = IntentDecision(
                intent=intent,
                route=route,
                confidence=winner.score,
                layer_hit=winner.layer,
                passed=True,
                failure_reason="",
            )
        return FunnelResult(
            decision=decision, attempts=attempts, latency_ms=_millis(started)
        )


def default_funnel() -> IntentFunnel:
    return IntentFunnel()


_UNCLEAR = "这句话还不够明确，请说明要查的制度，或要办理的事。"
_UNRECOGNIZED = "系统没有识别成这句话，请换一种说法再试一次。"
_RECOGNITION_FAILURES = frozenset({"parse_error", "schema_error", "model_error"})
_RECORDED_FAILURES = _RECOGNITION_FAILURES | {"business_confidence"}


def unknown_user_message(attempts: list[LayerAttempt]) -> str:
    """Low confidence blames the sentence. A broken model reply blames recognition."""

    if _last_model_failure(attempts) in _RECOGNITION_FAILURES:
        return _UNRECOGNIZED
    return _UNCLEAR


def _recorded_failure(attempts: list[LayerAttempt], *, passed: bool) -> str:
    if passed:
        return ""
    reason = _last_model_failure(attempts)
    if reason in _RECORDED_FAILURES:
        return reason
    return ""


def _last_model_failure(attempts: list[LayerAttempt]) -> str:
    for attempt in reversed(attempts):
        if attempt.layer in {IntentLayer.SMALL_LLM, IntentLayer.LARGE_LLM}:
            return attempt.failure_reason
    return ""


def _earlier_result(attempts: list[LayerAttempt]) -> LayerAttempt | None:
    for layer in (IntentLayer.RULES, IntentLayer.VECTOR):
        for attempt in attempts:
            if attempt.layer is layer and attempt.candidate:
                return LayerAttempt(
                    layer=attempt.layer,
                    passed=True,
                    score=attempt.score,
                    latency_ms=attempt.latency_ms,
                    intent=attempt.candidate,
                )
    return None


def _route(
    intent: str, confidence: float, second_intent: str | None, knowledge_min: float
) -> IntentRoute:
    if intent == INTENT_UNKNOWN:
        return IntentRoute.UNKNOWN
    single_knowledge = (
        intent in KNOWLEDGE_INTENTS
        and not second_intent
        and confidence >= knowledge_min
    )
    if single_knowledge:
        return IntentRoute.KNOWLEDGE
    return IntentRoute.ORCHESTRATE


def _millis(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
