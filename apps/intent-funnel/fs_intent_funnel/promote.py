"""Offline pass: repeated large-model fallbacks become vector examples.

This does not run while a request is being classified.
"""

from __future__ import annotations

from fs_intent_funnel.log import DecisionLog
from fs_intent_funnel.rules import rule_scores
from fs_intent_funnel.vectors import MemoryIntentIndex


def promote_repeated(log: DecisionLog, index: MemoryIntentIndex, *, min_repeats: int = 3) -> list[str]:
    added: list[str] = []
    for text, intent in log.repeated_fallbacks(min_repeats=min_repeats):
        index.add(text, intent)
        added.append(text)
    return added


def rule_candidates(log: DecisionLog, *, min_repeats: int = 3) -> list[tuple[str, str]]:
    """Fallback phrases that still have no keyword rule. The caller stores them."""

    found: list[tuple[str, str]] = []
    for text, intent in log.repeated_fallbacks(min_repeats=min_repeats):
        if intent not in rule_scores(text):
            found.append((text, intent))
    return found
