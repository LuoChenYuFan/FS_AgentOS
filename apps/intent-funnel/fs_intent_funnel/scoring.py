"""Shared pass rule: the leader must clear a floor and a gap to second place."""

from __future__ import annotations


def clears_margin(scores: list[float], *, minimum: float, margin: float) -> bool:
    if not scores:
        return False
    ordered = sorted(scores, reverse=True)
    leader = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else 0.0
    return leader >= minimum and (leader - runner_up) >= margin
