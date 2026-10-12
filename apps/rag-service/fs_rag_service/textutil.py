"""Chunking and a deterministic vector so Milvus has something to store."""

from __future__ import annotations

import hashlib

# 中文事实问答按段落成片。2048 会把联系方式、技能、工作和项目压进同一段，问一句就整段回来。
_CHUNK_SIZE = 112
_CHUNK_OVERLAP = 24
_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ". ", " ", "")


def tokens(text: str) -> list[str]:
    compact = "".join(text.lower().split())
    if len(compact) < 2:
        return [compact] if compact else []
    return [compact[index : index + 2] for index in range(len(compact) - 1)]


def chunk_text(
    text: str,
    size: int = _CHUNK_SIZE,
    overlap: int = _CHUNK_OVERLAP,
) -> list[str]:
    """Split on paragraph and sentence boundaries, then overlap neighboring slices."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk overlap must be smaller than the chunk size")
    pieces = _recursive_splits(cleaned, size, _SEPARATORS)
    return _merge_splits(pieces, size, overlap)


def embed(text: str, dim: int = 32) -> list[float]:
    vector = [0.0] * dim
    for token in tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def cache_key(tenant_id: str, query: str, index_version: str) -> str:
    return f"rag:{index_version}:{tenant_id}:{normalize_query(query)}"


def _recursive_splits(text: str, size: int, separators: tuple[str, ...]) -> list[str]:
    if len(text) <= size:
        return [text]
    separator, remaining = _next_separator(text, separators)
    if separator == "":
        return [text[index : index + 1] for index in range(len(text))]
    raw_parts = text.split(separator)
    parts: list[str] = []
    for index, part in enumerate(raw_parts):
        piece = part if index == len(raw_parts) - 1 else f"{part}{separator}"
        if piece:
            parts.append(piece)
    splits: list[str] = []
    for part in parts:
        if len(part) <= size:
            splits.append(part)
        else:
            splits.extend(_recursive_splits(part, size, remaining))
    return splits


def _next_separator(
    text: str, separators: tuple[str, ...]
) -> tuple[str, tuple[str, ...]]:
    for index, separator in enumerate(separators):
        if separator == "":
            return "", ()
        if separator in text:
            return separator, separators[index + 1 :]
    return "", ()


def _merge_splits(splits: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    total = 0
    for split in splits:
        length = len(split)
        if current and total + length > size:
            chunk = "".join(current).strip()
            if chunk:
                chunks.append(chunk)
            while current and (total > overlap or total + length > size):
                total -= len(current[0])
                current.pop(0)
        current.append(split)
        total += length
    tail = "".join(current).strip()
    if tail:
        chunks.append(tail)
    return chunks
