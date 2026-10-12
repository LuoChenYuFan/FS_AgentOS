"""Counts from the decision log, and a fixed sentence list for the first valid reply."""

from __future__ import annotations

from typing import Protocol

EVAL_UTTERANCES = (
    "帮我查询明天北京天气",
    "年假有几天",
    "帮我发一封邮件",
    "查一下员工手册里的差旅标准",
    "今天怎么样呀",
)


class _Classified(Protocol):
    last_tries: list[str]

    def classify(self, text: str) -> object: ...


def measure_first_try(
    model: _Classified, utterances: tuple[str, ...] = EVAL_UTTERANCES
) -> float | None:
    """Share of sentences whose first model reply was already valid."""

    tries = []
    for text in utterances:
        model.classify(text)
        tries.append(list(model.last_tries))
    return first_try_json_rate(tries)


def first_try_json_rate(tries_per_call: list[list[str]]) -> float | None:
    if not tries_per_call:
        return None
    ok = sum(1 for tries in tries_per_call if tries and tries[0] == "")
    return ok / len(tries_per_call)


def failure_reason_counts(reasons: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in reasons:
        if not reason:
            continue
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def reasons_over_count(counts: dict[str, int], *, limit: int) -> list[str]:
    return sorted(name for name, count in counts.items() if count >= limit)
