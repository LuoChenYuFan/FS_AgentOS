"""Intent examples. Production stores them in a Milvus collection; this memory index uses the same search result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IntentHit:
    intent: str
    score: float


class IntentExampleIndex(Protocol):
    def search(self, text: str, *, limit: int = 2) -> list[IntentHit]: ...


class MemoryIntentIndex:
    """Character-bigram similarity, collapsed to the best score of each intent."""

    def __init__(self, examples: list[tuple[str, str]] | None = None) -> None:
        self._examples = list(examples or _DEFAULT_EXAMPLES)

    def add(self, text: str, intent: str) -> None:
        self._examples.append((text, intent))

    def search(self, text: str, *, limit: int = 2) -> list[IntentHit]:
        best: dict[str, float] = {}
        for sample, intent in self._examples:
            score = _cosine(text, sample)
            if score > best.get(intent, 0.0):
                best[intent] = score
        ordered = sorted(best.items(), key=lambda item: item[1], reverse=True)
        return [IntentHit(intent=name, score=score) for name, score in ordered[:limit]]


_DEFAULT_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("年假剩余天数按员工手册哪一条", "knowledge"),
    ("帮我订周五下午的会议室", "schedule"),
)


def _bigrams(text: str) -> dict[str, int]:
    compact = "".join(text.lower().split())
    counts: dict[str, int] = {}
    for index in range(len(compact) - 1):
        gram = compact[index : index + 2]
        counts[gram] = counts.get(gram, 0) + 1
    return counts


def _cosine(left: str, right: str) -> float:
    first = _bigrams(left)
    second = _bigrams(right)
    if not first or not second:
        return 0.0
    dot = sum(count * second[gram] for gram, count in first.items() if gram in second)
    left_norm = sum(count * count for count in first.values()) ** 0.5
    right_norm = sum(count * count for count in second.values()) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
