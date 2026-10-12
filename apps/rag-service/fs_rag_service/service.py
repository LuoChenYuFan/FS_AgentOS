"""Query path: cache, then BM25 and vectors together, then graph, then rerank.

Ingest is separate. It writes the file to object storage before the indexes.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fs_common import new_id
from fs_common.workers import run_io
from fs_rag_sdk import Citation, RetrievedContext, RetrieveRequest, RetrieveResult

from fs_rag_service.catalog import PostgresCatalog
from fs_rag_service.embeddings import (
    DashScopeEmbedder,
    HashEmbedder,
    dashscope_embedder_from_env,
)
from fs_rag_service.errors import RetrievalBackendError, RetrievalError
from fs_rag_service.files import extract_text, media_type_for
from fs_rag_service.memory import (
    Chunk,
    FusedHit,
    IdentityReranker,
    MemoryCatalog,
    MemoryGraph,
    MemoryLexicalIndex,
    MemoryObjectStore,
    MemoryQueryCache,
    MemoryVectorIndex,
    fuse,
)
from fs_rag_service.rerank import DashScopeReranker, dashscope_reranker_from_env
from fs_rag_service.textutil import cache_key, chunk_text

INDEX_VERSION = "v1"
_TTL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class StoreFailure:
    store: str
    reason: str


def _store_attempt(store: str, action: Callable[[], None]) -> StoreFailure | None:
    try:
        action()
    except Exception as exc:
        return StoreFailure(store, _failure_reason(exc))
    return None


def _failure_reason(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 4:
        seen.add(id(current))
        text = " ".join(str(current).split())
        if text and text not in parts:
            parts.append(text)
        current = current.__cause__
    reason = "；".join(parts) if parts else type(exc).__name__
    return reason[:400]


class RagService:
    def __init__(
        self,
        *,
        objects: MemoryObjectStore,
        catalog: MemoryCatalog | PostgresCatalog,
        lexical: MemoryLexicalIndex,
        vectors: MemoryVectorIndex,
        graph: MemoryGraph,
        cache: MemoryQueryCache,
        reranker: IdentityReranker | DashScopeReranker | None = None,
        embedder: HashEmbedder | DashScopeEmbedder | None = None,
        index_version: str = INDEX_VERSION,
        ttl_seconds: int = _TTL_SECONDS,
    ) -> None:
        self.objects = objects
        self.catalog = catalog
        self.lexical = lexical
        self.vectors = vectors
        self.graph = graph
        self.cache = cache
        self.reranker = reranker or IdentityReranker()
        self.embedder = embedder or HashEmbedder()
        self.index_version = index_version
        self.ttl_seconds = ttl_seconds

    def list_documents(self, *, tenant_id: str) -> list[dict[str, str | int]]:
        return self.catalog.list_documents(tenant_id=tenant_id)

    def delete_document(self, *, tenant_id: str, document_id: str) -> list[StoreFailure]:
        """Remove one document from Milvus, MinIO, Elasticsearch, and PostgreSQL.

        Milvus, Elasticsearch, MinIO, and the graph are all attempted. PostgreSQL
        is removed only after those succeed, so a failed store can be retried
        while the chunk ids still exist.
        """
        if not tenant_id or not document_id:
            raise ValueError("tenant_id and document_id are required")
        try:
            record = self.catalog.get_document(document_id, tenant_id=tenant_id)
        except Exception as exc:
            return [StoreFailure("PostgreSQL", _failure_reason(exc))]
        if record is None:
            raise LookupError("document not found")
        try:
            chunk_ids = self.catalog.chunk_ids(document_id)
        except Exception as exc:
            return [StoreFailure("PostgreSQL", _failure_reason(exc))]
        failures = [
            failure
            for failure in (
                _store_attempt("Milvus", lambda: self.vectors.delete(tenant_id, chunk_ids)),
                _store_attempt(
                    "Elasticsearch", lambda: self.lexical.delete(tenant_id, chunk_ids)
                ),
                _store_attempt("MinIO", lambda: self.objects.delete(record["oss_key"])),
                _store_attempt("Neo4j", lambda: self.graph.drop(tenant_id, chunk_ids)),
            )
            if failure is not None
        ]
        if not failures:
            failures.extend(
                item
                for item in (
                    _store_attempt(
                        "PostgreSQL",
                        lambda: self.catalog.delete_document(
                            document_id, tenant_id=tenant_id
                        ),
                    ),
                )
                if item is not None
            )
        try:
            self.cache.drop_tenant(tenant_id, self.index_version)
        except Exception:
            pass
        return failures

    def ingest_text(
        self,
        *,
        tenant_id: str,
        title: str,
        text: str,
        media_type: str = "text/plain",
        entities: list[tuple[str, str]] | None = None,
        relations: list[tuple[str, str, str]] | None = None,
    ) -> str:
        cleaned = text.strip()
        if not tenant_id or not title.strip() or not cleaned:
            raise ValueError("tenant_id, title, and text are required")
        return self._store(
            tenant_id=tenant_id,
            title=title.strip(),
            text=cleaned,
            payload=cleaned.encode("utf-8"),
            media_type=media_type,
            entities=entities,
            relations=relations,
        )

    def ingest_file(self, *, tenant_id: str, filename: str, body: bytes) -> str:
        name = Path(filename).name.strip()
        if not tenant_id or not name or not body:
            raise ValueError("tenant_id, filename, and file bytes are required")
        text = extract_text(name, body)
        return self._store(
            tenant_id=tenant_id,
            title=name,
            text=text,
            payload=body,
            media_type=media_type_for(name),
        )

    def _store(
        self,
        *,
        tenant_id: str,
        title: str,
        text: str,
        payload: bytes,
        media_type: str,
        entities: list[tuple[str, str]] | None = None,
        relations: list[tuple[str, str, str]] | None = None,
    ) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = self.catalog.find_by_title(tenant_id=tenant_id, title=title)
        if existing is not None and existing["content_hash"] == digest:
            return existing["document_id"]
        pieces = chunk_text(text) or [text]
        vectors = self.embedder.embed_many(pieces)
        return self._persist(
            tenant_id=tenant_id,
            title=title,
            text=text,
            payload=payload,
            media_type=media_type,
            entities=entities,
            relations=relations,
            pieces=pieces,
            vectors=vectors,
            existing=existing,
            digest=digest,
        )

    async def aingest_text(
        self,
        *,
        tenant_id: str,
        title: str,
        text: str,
        media_type: str = "text/plain",
        entities: list[tuple[str, str]] | None = None,
        relations: list[tuple[str, str, str]] | None = None,
    ) -> str:
        cleaned = text.strip()
        if not tenant_id or not title.strip() or not cleaned:
            raise ValueError("tenant_id, title, and text are required")
        return await self._astore(
            tenant_id=tenant_id,
            title=title.strip(),
            text=cleaned,
            payload=cleaned.encode("utf-8"),
            media_type=media_type,
            entities=entities,
            relations=relations,
        )

    async def aingest_file(self, *, tenant_id: str, filename: str, body: bytes) -> str:
        name = Path(filename).name.strip()
        if not tenant_id or not name or not body:
            raise ValueError("tenant_id, filename, and file bytes are required")
        text = await run_io(extract_text, name, body)
        return await self._astore(
            tenant_id=tenant_id,
            title=name,
            text=text,
            payload=body,
            media_type=media_type_for(name),
        )

    async def _astore(
        self,
        *,
        tenant_id: str,
        title: str,
        text: str,
        payload: bytes,
        media_type: str,
        entities: list[tuple[str, str]] | None = None,
        relations: list[tuple[str, str, str]] | None = None,
    ) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = await run_io(self.catalog.find_by_title, tenant_id=tenant_id, title=title)
        if existing is not None and existing["content_hash"] == digest:
            return existing["document_id"]
        pieces = chunk_text(text) or [text]
        vectors = await _embed_many(self.embedder, pieces)
        return await run_io(
            self._persist,
            tenant_id=tenant_id,
            title=title,
            text=text,
            payload=payload,
            media_type=media_type,
            entities=entities,
            relations=relations,
            pieces=pieces,
            vectors=vectors,
            existing=existing,
            digest=digest,
        )

    def _persist(
        self,
        *,
        tenant_id: str,
        title: str,
        text: str,
        payload: bytes,
        media_type: str,
        pieces: list[str],
        vectors: list[list[float]],
        existing: dict[str, str] | None,
        digest: str,
        entities: list[tuple[str, str]] | None = None,
        relations: list[tuple[str, str, str]] | None = None,
    ) -> str:
        del text
        if existing is None:
            document_id = new_id("doc")
            oss_key = f"{tenant_id}/{document_id}"
        else:
            document_id = existing["document_id"]
            oss_key = existing["oss_key"]
            self._forget(tenant_id, document_id)
        source_uri = self.objects.put(oss_key, payload, media_type=media_type)
        self.catalog.save_document(
            document_id=document_id,
            tenant_id=tenant_id,
            title=title,
            oss_key=oss_key,
            source_uri=source_uri,
            media_type=media_type,
            index_version=self.index_version,
            content_hash=digest,
        )
        for ordinal, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
            entity_id = _entity_for(piece, entities)
            chunk = Chunk(
                chunk_id=new_id("chunk"),
                document_id=document_id,
                tenant_id=tenant_id,
                text=piece,
                ordinal=ordinal,
                source_uri=source_uri,
                entity_id=entity_id,
            )
            self.catalog.save_chunk(chunk)
            self.lexical.index(chunk)
            self.vectors.index(chunk, vector)
            if entity_id:
                self.graph.mention(tenant_id, chunk.chunk_id, entity_id)
        self.lexical.flush()
        self.vectors.flush()
        for source, relation, target in relations or []:
            self.graph.relate(tenant_id, source, relation, target)
        self.cache.drop_tenant(tenant_id, self.index_version)
        return document_id

    def _forget(self, tenant_id: str, document_id: str) -> None:
        chunk_ids = self.catalog.chunk_ids(document_id)
        self.lexical.delete(tenant_id, chunk_ids)
        self.vectors.delete(tenant_id, chunk_ids)
        self.graph.drop(tenant_id, chunk_ids)
        self.catalog.delete_chunks(document_id)

    def retrieve_blocking(self, request: RetrieveRequest) -> RetrieveResult:
        tenant_id = request.tenant_id or request.workspace_id or ""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        key = cache_key(tenant_id, request.query, self.index_version)
        cached = self.cache.get(key)
        if cached is not None:
            restored = RetrieveResult.model_validate_json(cached)
            return restored.model_copy(update={"cache_hit": True})
        bm25_ids, vector_ids = self._recall(tenant_id, request.query, request.top_k)
        fused = fuse(bm25_ids, vector_ids)
        extra = self._expand(tenant_id, [hit.chunk_id for hit in fused], request.top_k)
        prepared = self._attach_text(tenant_id, _with_graph(fused, extra))
        try:
            ranked = self.reranker.rerank(request.query, prepared)
        except RetrievalBackendError:
            ranked = prepared
        ranked = ranked[: request.top_k]
        citations = self._citations(tenant_id, ranked)
        context = RetrievedContext(query=request.query, citations=citations)
        result = RetrieveResult(
            citations=citations,
            retrieved_context=context,
            cache_hit=False,
            channels=_channels(ranked),
        )
        self.cache.put(key, result.model_dump_json(), ttl_seconds=self.ttl_seconds)
        return result

    async def retrieve(self, request: RetrieveRequest) -> RetrieveResult:
        tenant_id = request.tenant_id or request.workspace_id or ""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        key = cache_key(tenant_id, request.query, self.index_version)
        cached = await run_io(self.cache.get, key)
        if cached is not None:
            restored = RetrieveResult.model_validate_json(cached)
            return restored.model_copy(update={"cache_hit": True})
        vector = await _embed_one(self.embedder, request.query)
        lexical_ids, vector_ids = await asyncio.gather(
            run_io(self._search_lexical, tenant_id, request.query, request.top_k),
            run_io(self._search_vectors, tenant_id, vector, request.top_k),
        )
        if lexical_ids is None and vector_ids is None:
            raise RetrievalError("lexical and vector retrieval both failed")
        fused = fuse(lexical_ids or [], vector_ids or [])
        extra = await run_io(
            self._expand, tenant_id, [hit.chunk_id for hit in fused], request.top_k
        )
        prepared = await run_io(self._attach_text, tenant_id, _with_graph(fused, extra))
        try:
            ranked = await _rerank(self.reranker, request.query, prepared)
        except RetrievalBackendError:
            ranked = prepared
        ranked = ranked[: request.top_k]
        citations = await run_io(self._citations, tenant_id, ranked)
        context = RetrievedContext(query=request.query, citations=citations)
        result = RetrieveResult(
            citations=citations,
            retrieved_context=context,
            cache_hit=False,
            channels=_channels(ranked),
        )
        await run_io(self.cache.put, key, result.model_dump_json(), ttl_seconds=self.ttl_seconds)
        return result

    def _search_lexical(self, tenant_id: str, query: str, limit: int) -> list[str] | None:
        return _attempt(lambda: self.lexical.search(tenant_id=tenant_id, query=query, limit=limit))

    def _search_vectors(
        self, tenant_id: str, vector: list[float], limit: int
    ) -> list[str] | None:
        return _attempt(
            lambda: self.vectors.search(tenant_id=tenant_id, vector=vector, limit=limit)
        )

    def _recall(
        self, tenant_id: str, query: str, limit: int
    ) -> tuple[list[str], list[str]]:
        lexical_ids = _attempt(
            lambda: self.lexical.search(tenant_id=tenant_id, query=query, limit=limit)
        )
        vector_ids = _attempt(
            lambda: self.vectors.search(
                tenant_id=tenant_id, vector=self.embedder.embed(query), limit=limit
            )
        )
        if lexical_ids is None and vector_ids is None:
            raise RetrievalError("lexical and vector retrieval both failed")
        return lexical_ids or [], vector_ids or []

    def _expand(self, tenant_id: str, chunk_ids: list[str], limit: int) -> list[str]:
        if not chunk_ids:
            return []
        try:
            return self.graph.expand(chunk_ids, tenant_id=tenant_id, limit=limit)
        except (RetrievalBackendError, OSError, RuntimeError, ValueError, TypeError):
            return []

    def _attach_text(self, tenant_id: str, hits: list[FusedHit]) -> list[FusedHit]:
        attached: list[FusedHit] = []
        for hit in hits:
            chunk = self.catalog.get_chunk(hit.chunk_id, tenant_id=tenant_id)
            text = chunk.text if chunk is not None else hit.text
            attached.append(FusedHit(hit.chunk_id, hit.score, list(hit.channels), text))
        return attached

    def _citations(self, tenant_id: str, hits: list[FusedHit]) -> list[Citation]:
        citations: list[Citation] = []
        for hit in hits:
            chunk = self.catalog.get_chunk(hit.chunk_id, tenant_id=tenant_id)
            if chunk is None:
                continue
            citations.append(
                Citation(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    score=hit.score,
                    source_uri=chunk.source_uri,
                    url=chunk.source_uri,
                    entity_id=chunk.entity_id,
                    channels=list(hit.channels),
                )
            )
        return citations


def build_memory_rag() -> RagService:
    return RagService(
        objects=MemoryObjectStore(),
        catalog=MemoryCatalog(),
        lexical=MemoryLexicalIndex(),
        vectors=MemoryVectorIndex(),
        graph=MemoryGraph(),
        cache=MemoryQueryCache(),
    )


def build_rag_service() -> RagService:
    """Use a configured backend when its address is set, otherwise the memory stand-in."""
    from fs_orchestrator.postgres import load_local_env, postgres_configured

    load_local_env()
    objects = object_store_from_env()
    catalog: MemoryCatalog | PostgresCatalog = (
        PostgresCatalog() if postgres_configured() else MemoryCatalog()
    )
    lexical: MemoryLexicalIndex = MemoryLexicalIndex()
    if os.environ.get("ELASTICSEARCH_URL"):
        from fs_rag_service.backends import EsLexicalIndex

        lexical = EsLexicalIndex(os.environ["ELASTICSEARCH_URL"])
    embedder = dashscope_embedder_from_env() or HashEmbedder()
    vectors: MemoryVectorIndex = MemoryVectorIndex()
    if os.environ.get("MILVUS_URI"):
        from fs_rag_service.backends import MilvusVectorIndex

        vectors = MilvusVectorIndex(os.environ["MILVUS_URI"], dim=embedder.dim)
    graph: MemoryGraph = MemoryGraph()
    if os.environ.get("NEO4J_URI"):
        from fs_rag_service.backends import Neo4jGraph

        graph = Neo4jGraph(
            os.environ["NEO4J_URI"],
            os.environ.get("NEO4J_USER", ""),
            os.environ.get("NEO4J_PASSWORD", ""),
        )
    cache: MemoryQueryCache = MemoryQueryCache()
    if os.environ.get("REDIS_URL"):
        from fs_rag_service.backends import RedisQueryCache

        cache = RedisQueryCache(os.environ["REDIS_URL"])
    return RagService(
        objects=objects,
        catalog=catalog,
        lexical=lexical,
        vectors=vectors,
        graph=graph,
        cache=cache,
        reranker=dashscope_reranker_from_env(),
        embedder=embedder,
    )


def object_store_from_env() -> MemoryObjectStore:
    """Aliyun OSS wins when it is configured. MinIO is the local stand-in."""
    from fs_orchestrator.postgres import load_local_env

    load_local_env()
    if os.environ.get("OSS_ENDPOINT") and os.environ.get("OSS_BUCKET"):
        from fs_rag_service.backends import AliyunObjectStore

        return AliyunObjectStore(
            os.environ["OSS_ENDPOINT"],
            os.environ["OSS_BUCKET"],
            os.environ.get("OSS_ACCESS_KEY_ID", ""),
            os.environ.get("OSS_ACCESS_KEY_SECRET", ""),
        )
    if os.environ.get("MINIO_ENDPOINT") and os.environ.get("MINIO_BUCKET"):
        from fs_rag_service.backends import MinioObjectStore

        secure = os.environ.get("MINIO_SECURE", "false").lower() in {"1", "true", "yes"}
        return MinioObjectStore(
            os.environ["MINIO_ENDPOINT"],
            os.environ["MINIO_BUCKET"],
            os.environ.get("MINIO_ACCESS_KEY", ""),
            os.environ.get("MINIO_SECRET_KEY", ""),
            secure=secure,
        )
    return MemoryObjectStore()


async def _embed_one(embedder: HashEmbedder | DashScopeEmbedder, text: str) -> list[float]:
    return await embedder.aembed(text)


async def _embed_many(
    embedder: HashEmbedder | DashScopeEmbedder, texts: list[str]
) -> list[list[float]]:
    return await embedder.aembed_many(texts)


async def _rerank(
    reranker: IdentityReranker | DashScopeReranker, query: str, hits: list[FusedHit]
) -> list[FusedHit]:
    return await reranker.arerank(query, hits)


def _attempt(call) -> list[str] | None:
    try:
        return call()
    except RetrievalBackendError:
        return None


def _with_graph(hits: list[FusedHit], extra: list[str]) -> list[FusedHit]:
    seen = {hit.chunk_id for hit in hits}
    merged = list(hits)
    for chunk_id in extra:
        if chunk_id in seen:
            for hit in merged:
                if hit.chunk_id == chunk_id and "graph" not in hit.channels:
                    hit.channels.append("graph")
            continue
        seen.add(chunk_id)
        merged.append(FusedHit(chunk_id, 0.0001, ["graph"]))
    return merged


def _channels(hits: list[FusedHit]) -> list[str]:
    names: list[str] = []
    for hit in hits:
        for channel in hit.channels:
            if channel not in names:
                names.append(channel)
    return names


def _entity_for(text: str, entities: list[tuple[str, str]] | None) -> str | None:
    for entity_id, name in entities or []:
        if name and name in text:
            return entity_id
    return None
