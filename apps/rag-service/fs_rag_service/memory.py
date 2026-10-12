"""In-process stand-ins for OSS, Redis, Elasticsearch, Milvus, and Neo4j.

They speak the same operations as the external backends. The query pipeline
does not care which one is plugged in.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from fs_rag_service.textutil import cosine, embed, tokens


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    tenant_id: str
    text: str
    ordinal: int
    source_uri: str
    entity_id: str | None = None


class MemoryObjectStore:
    def __init__(self) -> None:
        self.bodies: dict[str, bytes] = {}

    def put(self, key: str, body: bytes, *, media_type: str) -> str:
        del media_type
        self.bodies[key] = body
        return f"oss://local/{key}"

    def delete(self, key: str) -> None:
        self.bodies.pop(key, None)


class MemoryCatalog:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, str]] = {}
        self.chunks: dict[str, Chunk] = {}

    def save_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        title: str,
        oss_key: str,
        source_uri: str,
        media_type: str,
        index_version: str,
        content_hash: str,
    ) -> None:
        self.documents[document_id] = {
            "tenant_id": tenant_id,
            "title": title,
            "oss_key": oss_key,
            "source_uri": source_uri,
            "media_type": media_type,
            "index_version": index_version,
            "content_hash": content_hash,
        }

    def list_documents(self, *, tenant_id: str) -> list[dict[str, str | int]]:
        rows: list[dict[str, str | int]] = []
        for document_id, row in self.documents.items():
            if row["tenant_id"] != tenant_id:
                continue
            rows.append(
                {
                    "document_id": document_id,
                    "title": row["title"],
                    "media_type": row["media_type"],
                    "chunk_count": sum(
                        1 for chunk in self.chunks.values() if chunk.document_id == document_id
                    ),
                    "created_at": "",
                }
            )
        rows.reverse()
        return rows

    def get_document(self, document_id: str, *, tenant_id: str) -> dict[str, str] | None:
        row = self.documents.get(document_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return {
            "document_id": document_id,
            "title": row["title"],
            "oss_key": row["oss_key"],
        }

    def delete_document(self, document_id: str, *, tenant_id: str) -> None:
        row = self.documents.get(document_id)
        if row is None or row["tenant_id"] != tenant_id:
            return
        self.delete_chunks(document_id)
        self.documents.pop(document_id, None)

    def find_by_title(self, *, tenant_id: str, title: str) -> dict[str, str] | None:
        for document_id, row in self.documents.items():
            if row["tenant_id"] == tenant_id and row["title"] == title:
                return {"document_id": document_id, **row}
        return None

    def chunk_ids(self, document_id: str) -> list[str]:
        return [
            chunk.chunk_id
            for chunk in self.chunks.values()
            if chunk.document_id == document_id
        ]

    def delete_chunks(self, document_id: str) -> None:
        for chunk_id in self.chunk_ids(document_id):
            self.chunks.pop(chunk_id, None)

    def save_chunk(self, chunk: Chunk) -> None:
        self.chunks[chunk.chunk_id] = chunk

    def get_chunk(self, chunk_id: str, *, tenant_id: str) -> Chunk | None:
        chunk = self.chunks.get(chunk_id)
        if chunk is None or chunk.tenant_id != tenant_id:
            return None
        return chunk


class MemoryLexicalIndex:
    """BM25 over character bigrams. This is the Elasticsearch role."""

    def __init__(self) -> None:
        self.calls = 0
        self._docs: dict[str, list[tuple[str, list[str]]]] = {}

    def index(self, chunk: Chunk) -> None:
        self._docs.setdefault(chunk.tenant_id, []).append(
            (chunk.chunk_id, tokens(chunk.text))
        )

    def flush(self) -> None:
        return None

    def delete(self, tenant_id: str, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        gone = set(chunk_ids)
        rows = self._docs.get(tenant_id, [])
        self._docs[tenant_id] = [row for row in rows if row[0] not in gone]

    def search(self, *, tenant_id: str, query: str, limit: int) -> list[str]:
        self.calls += 1
        rows = self._docs.get(tenant_id, [])
        if not rows:
            return []
        query_terms = tokens(query)
        document_frequency: dict[str, int] = {}
        for _, doc_terms in rows:
            for term in set(doc_terms):
                document_frequency[term] = document_frequency.get(term, 0) + 1
        total = len(rows)
        average_length = sum(len(doc_terms) for _, doc_terms in rows) / total
        scored: list[tuple[float, str]] = []
        for chunk_id, doc_terms in rows:
            score = _bm25(
                query_terms, doc_terms, average_length, total, document_frequency
            )
            if score > 0:
                scored.append((score, chunk_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk_id for _, chunk_id in scored[:limit]]


class MemoryVectorIndex:
    """Cosine search. This is the Milvus role."""

    def __init__(self) -> None:
        self.calls = 0
        self._rows: dict[str, list[tuple[str, list[float]]]] = {}

    def index(self, chunk: Chunk, vector: list[float] | None = None) -> None:
        stored = vector if vector is not None else embed(chunk.text)
        self._rows.setdefault(chunk.tenant_id, []).append((chunk.chunk_id, stored))

    def flush(self) -> None:
        return None

    def delete(self, tenant_id: str, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        gone = set(chunk_ids)
        rows = self._rows.get(tenant_id, [])
        self._rows[tenant_id] = [row for row in rows if row[0] not in gone]

    def search(self, *, tenant_id: str, vector: list[float], limit: int) -> list[str]:
        self.calls += 1
        scored = []
        for chunk_id, stored in self._rows.get(tenant_id, []):
            score = cosine(vector, stored)
            if score > 0:
                scored.append((score, chunk_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk_id for _, chunk_id in scored[:limit]]


class MemoryGraph:
    """Entity links. Expansion adds chunks; it does not drop the ones already found."""

    def __init__(self) -> None:
        self._mentions: dict[str, dict[str, set[str]]] = {}
        self._chunks_for_entity: dict[str, dict[str, set[str]]] = {}
        self._related: dict[str, dict[str, set[str]]] = {}

    def mention(self, tenant_id: str, chunk_id: str, entity_id: str) -> None:
        self._mentions.setdefault(tenant_id, {}).setdefault(chunk_id, set()).add(
            entity_id
        )
        self._chunks_for_entity.setdefault(tenant_id, {}).setdefault(
            entity_id, set()
        ).add(chunk_id)

    def relate(self, tenant_id: str, source: str, relation: str, target: str) -> None:
        del relation
        self._related.setdefault(tenant_id, {}).setdefault(source, set()).add(target)

    def drop(self, tenant_id: str, chunk_ids: list[str]) -> None:
        mentions = self._mentions.get(tenant_id, {})
        owners = self._chunks_for_entity.get(tenant_id, {})
        for chunk_id in chunk_ids:
            for entity_id in mentions.pop(chunk_id, set()):
                owners.get(entity_id, set()).discard(chunk_id)

    def expand(self, chunk_ids: list[str], *, tenant_id: str, limit: int) -> list[str]:
        mentions = self._mentions.get(tenant_id, {})
        related = self._related.get(tenant_id, {})
        owners = self._chunks_for_entity.get(tenant_id, {})
        entities: set[str] = set()
        for chunk_id in chunk_ids:
            entities.update(mentions.get(chunk_id, set()))
        expanded = set(entities)
        for entity_id in entities:
            expanded.update(related.get(entity_id, set()))
        found: list[str] = []
        seen = set(chunk_ids)
        for entity_id in expanded:
            for chunk_id in owners.get(entity_id, set()):
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                found.append(chunk_id)
                if len(found) >= limit:
                    return found
        return found


class MemoryQueryCache:
    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self._items: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._items.get(key)
        if item is None or item[0] <= time.monotonic():
            self.misses += 1
            self._items.pop(key, None)
            return None
        self.hits += 1
        return item[1]

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._items[key] = (time.monotonic() + ttl_seconds, value)

    def drop_tenant(self, tenant_id: str, index_version: str) -> None:
        prefix = f"rag:{index_version}:{tenant_id}:"
        for key in [key for key in self._items if key.startswith(prefix)]:
            self._items.pop(key, None)


class IdentityReranker:
    """Keeps the fused order. A later model swaps this object, not the pipeline."""

    def rerank(self, query: str, hits: list[FusedHit]) -> list[FusedHit]:
        del query
        return list(hits)

    async def arerank(self, query: str, hits: list[FusedHit]) -> list[FusedHit]:
        return self.rerank(query, hits)


@dataclass(slots=True)
class FusedHit:
    chunk_id: str
    score: float
    channels: list[str]
    text: str = ""


def fuse(bm25_ids: list[str], vector_ids: list[str], *, k: int = 60) -> list[FusedHit]:
    scores: dict[str, float] = {}
    channels: dict[str, list[str]] = {}
    for channel, ids in (("bm25", bm25_ids), ("vector", vector_ids)):
        for rank, chunk_id in enumerate(ids):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
            channels.setdefault(chunk_id, []).append(channel)
    ordered = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [
        FusedHit(chunk_id, scores[chunk_id], channels[chunk_id]) for chunk_id in ordered
    ]


def _bm25(
    query_terms: list[str],
    doc_terms: list[str],
    average_length: float,
    total_docs: int,
    document_frequency: dict[str, int],
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    if not doc_terms or average_length <= 0:
        return 0.0
    score = 0.0
    length = len(doc_terms)
    for term in set(query_terms):
        frequency = doc_terms.count(term)
        if frequency == 0:
            continue
        docs_with_term = document_frequency.get(term, 0)
        idf = math.log((total_docs - docs_with_term + 0.5) / (docs_with_term + 0.5) + 1)
        numerator = frequency * (k1 + 1)
        denominator = frequency + k1 * (1 - b + b * length / average_length)
        score += idf * numerator / denominator
    return score
