"""Layer 5: a cold query cites both search paths, and the next one is served from cache."""

import os
import uuid
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from fs_common.contracts.intent import IntentLayer
from fs_common.errors import ModelQuotaExhausted
from fs_gateway.context import GatewayState
from fs_gateway.main import create_app
from fs_gateway.settings import GatewaySettings
from fs_intent_funnel import IntentFunnel, MemoryDecisionLog, default_funnel
from fs_orchestrator import ThinOrchestrator
from fs_orchestrator.postgres import connect, migrate, postgres_configured
from fs_rag_sdk import RetrieveRequest
from fs_rag_service import (
    RagService,
    RetrievalError,
    RetrievingKnowledge,
    build_memory_rag,
)
from fs_rag_service.backends import (
    AliyunObjectStore,
    EsLexicalIndex,
    MilvusVectorIndex,
    MinioObjectStore,
)
from fs_rag_service.catalog import PostgresCatalog
from fs_rag_service.embeddings import DashScopeEmbedder
from fs_rag_service.errors import RetrievalBackendError
from fs_rag_service.files import extract_text
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
)
from fs_rag_service.rerank import DashScopeReranker
from fs_rag_service.service import build_rag_service, object_store_from_env
from fs_rag_service.textutil import chunk_text, cosine, embed


class SpyOrchestrator(ThinOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def run(
        self, *, task_id: str | None = None, input_payload: dict | None = None
    ) -> dict[str, str]:
        self.calls += 1
        return await super().run(task_id=task_id, input_payload=input_payload or {})


class BrokenSearch:
    def index(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def flush(self) -> None:
        return None

    def search(self, **kwargs: object) -> list[str]:
        del kwargs
        raise RetrievalBackendError("backend down")


class ReverseReranker(IdentityReranker):
    def rerank(self, query: str, hits: list) -> list:
        del query
        return list(reversed(hits))


def test_cold_query_cites_bm25_and_vectors_then_the_repeat_hits_cache() -> None:
    rag = build_memory_rag()
    rag.ingest_text(
        tenant_id="tenant_a", title="员工手册", text="员工年假每年五天，见制度说明。"
    )
    request = RetrieveRequest(query="员工年假", tenant_id="tenant_a")
    first = rag.retrieve_blocking(request)
    second = rag.retrieve_blocking(request)
    cited = first.citations[0]
    assert first.cache_hit is False
    assert "bm25" in cited.channels
    assert "vector" in cited.channels
    assert cited.chunk_id
    assert cited.source_uri.startswith("oss://")
    assert "年假" in cited.text
    assert first.retrieved_context is not None
    assert first.retrieved_context.role == "data"
    assert second.cache_hit is True
    assert second.citations[0].chunk_id == cited.chunk_id
    assert rag.lexical.calls == 1
    assert rag.vectors.calls == 1
    assert rag.cache.hits == 1


def test_spaced_repeat_uses_the_same_cache_entry() -> None:
    rag = build_memory_rag()
    rag.ingest_text(tenant_id="tenant_a", title="员工手册", text="员工年假每年五天。")
    rag.retrieve_blocking(RetrieveRequest(query="员工年假", tenant_id="tenant_a"))
    repeat = rag.retrieve_blocking(
        RetrieveRequest(query="  员工年假  ", tenant_id="tenant_a")
    )
    assert repeat.cache_hit is True
    assert rag.lexical.calls == 1


def test_another_tenant_does_not_see_the_passage() -> None:
    rag = build_memory_rag()
    rag.ingest_text(tenant_id="tenant_a", title="员工手册", text="员工年假每年五天。")
    foreign = rag.retrieve_blocking(
        RetrieveRequest(query="员工年假", tenant_id="tenant_b")
    )
    assert foreign.citations == []
    assert foreign.cache_hit is False


def test_both_backends_down_is_a_failure_and_is_not_cached() -> None:
    rag = _rag_with(lexical=BrokenSearch(), vectors=BrokenSearch())
    with pytest.raises(RetrievalError):
        rag.retrieve_blocking(RetrieveRequest(query="员工年假", tenant_id="tenant_a"))
    assert rag.cache.misses == 1
    assert rag.cache.hits == 0


def test_one_backend_can_fail_and_the_other_still_cites() -> None:
    rag = _rag_with(lexical=BrokenSearch())
    rag.ingest_text(tenant_id="tenant_a", title="员工手册", text="员工年假每年五天。")
    result = rag.retrieve_blocking(
        RetrieveRequest(query="员工年假", tenant_id="tenant_a")
    )
    assert result.channels == ["vector"]
    assert result.citations[0].text


def test_graph_adds_a_related_passage_without_dropping_the_hit() -> None:
    rag = build_memory_rag()
    rag.ingest_text(
        tenant_id="tenant_a",
        title="细则",
        text="年假细则见请假条目",
        entities=[("leave_rule", "年假细则")],
        relations=[("leave_rule", "broader", "leave_policy")],
    )
    rag.ingest_text(
        tenant_id="tenant_a",
        title="总则",
        text="请假总则每年五天",
        entities=[("leave_policy", "请假总则")],
    )
    direct = next(
        chunk for chunk in rag.catalog.chunks.values() if "年假细则" in chunk.text
    )

    def only_direct(**kwargs: object) -> list[str]:
        del kwargs
        return [direct.chunk_id]

    rag.lexical.search = only_direct  # type: ignore[method-assign]
    rag.vectors.search = only_direct  # type: ignore[method-assign]
    result = rag.retrieve_blocking(
        RetrieveRequest(query="年假细则", tenant_id="tenant_a")
    )
    related = next(
        item for item in result.citations if item.text.startswith("请假总则")
    )
    kept = next(item for item in result.citations if "年假细则" in item.text)
    assert "bm25" in kept.channels and "vector" in kept.channels
    assert related.channels == ["graph"]
    assert len(result.citations) == 2


def test_reranker_port_can_change_order() -> None:
    rag = build_memory_rag()
    rag.ingest_text(tenant_id="tenant_a", title="细则", text="年假细则说明")
    rag.ingest_text(tenant_id="tenant_a", title="总则", text="年假总则说明")
    plain = rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id="tenant_a"))
    flipped = _rag_with(reranker=ReverseReranker())
    flipped.ingest_text(tenant_id="tenant_a", title="细则", text="年假细则说明")
    flipped.ingest_text(tenant_id="tenant_a", title="总则", text="年假总则说明")
    reversed_result = flipped.retrieve_blocking(
        RetrieveRequest(query="年假", tenant_id="tenant_a")
    )
    assert [item.chunk_id for item in reversed_result.citations] != [
        item.chunk_id for item in plain.citations
    ]
    assert [item.text for item in reversed_result.citations] == list(
        reversed([item.text for item in plain.citations])
    )


def test_gateway_knowledge_route_returns_data_and_then_cache() -> None:
    rag = build_memory_rag()
    funnel = default_funnel()
    client, spy = _client(rag, funnel)
    headers = _auth(client)
    created = client.post(
        "/v1/documents",
        json={
            "title": "员工手册",
            "text": "查阅制度编号 HR-2024-001\n\n员工年假每年五天。",
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["document_id"]
    first = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=headers
    )
    second = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=headers
    )
    body = first.json()
    citation = body["retrieved_context"]["citations"][0]
    assert first.status_code == 200
    assert body["route"] == "knowledge"
    assert body["retrieved_context"]["role"] == "data"
    assert "bm25" in citation["channels"]
    assert "vector" in citation["channels"]
    assert citation["source_uri"].startswith("oss://")
    assert body["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert spy.calls == 0
    assert rag.lexical.calls == 1
    assert funnel.small_model.calls == 0
    assert funnel.large_model.calls == 0
    assert "reply" not in body


def test_knowledge_reply_is_written_from_the_passages() -> None:
    rag = build_memory_rag()
    chat = _RecordingChat("员工年假每年五天。")
    client, _spy = _client(rag, default_funnel(), rag_chat=chat)
    headers = _auth(client)
    created = client.post(
        "/v1/documents",
        json={"title": "员工手册", "text": "查阅制度编号 HR-2024-001\n\n员工年假每年五天。"},
        headers=headers,
    )
    assert created.status_code == 200
    asked = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=headers
    )
    assert asked.status_code == 200
    assert asked.json()["reply"] == "员工年假每年五天。"
    assert "员工年假每年五天。" in chat.messages[1]["content"]
    assert "不要编造" in chat.messages[0]["content"]

    chat.fail = True
    again = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=headers
    )
    passage = again.json()["retrieved_context"]["citations"][0]["text"].strip()
    assert again.json()["reply"] == passage


def test_exhausted_model_quota_is_returned_instead_of_the_passage() -> None:
    rag = build_memory_rag()
    client, _spy = _client(rag, default_funnel(), rag_chat=_QuotaChat("qwen3.7-plus"))
    headers = _auth(client)
    created = client.post(
        "/v1/documents",
        json={"title": "员工手册", "text": "查阅制度编号 HR-2024-001\n\n员工年假每年五天。"},
        headers=headers,
    )
    assert created.status_code == 200
    asked = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=headers
    )
    assert asked.status_code == 200
    assert asked.json()["reply"] == "qwen3.7-plus模型额度已用光，暂时无法使用该功能"


def test_short_text_stays_one_slice_and_long_text_overlaps() -> None:
    assert chunk_text("员工年假每年五天。") == ["员工年假每年五天。"]
    sentence = "年假需要提前申请。"
    pieces = chunk_text(sentence * 300)
    assert len(pieces) >= 2
    assert all(len(piece) <= 112 for piece in pieces)
    assert pieces[1][: len(sentence)] in pieces[0]


def test_same_title_replaces_the_old_text_and_clears_the_cache() -> None:
    rag = build_memory_rag()
    first = rag.ingest_text(
        tenant_id="tenant_a", title="规章制度.docx", text="公司年假7天"
    )
    rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id="tenant_a"))
    second = rag.ingest_text(
        tenant_id="tenant_a", title="规章制度.docx", text="公司年假10天"
    )
    result = rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id="tenant_a"))
    assert second == first
    assert len(rag.catalog.chunks) == 1
    assert result.cache_hit is False
    assert "10天" in result.citations[0].text
    assert "7天" not in result.citations[0].text
    again = rag.ingest_text(
        tenant_id="tenant_a", title="规章制度.docx", text="公司年假10天"
    )
    assert again == first
    assert len(rag.catalog.chunks) == 1


def test_uploaded_docx_is_stored_and_searched_as_its_text() -> None:
    rag = build_memory_rag()
    payload = _docx("星星公司规章制度", "公司年假7天")
    document_id = rag.ingest_file(
        tenant_id="tenant_a",
        filename="星星公司的规章制度.docx",
        body=payload,
    )
    stored = next(iter(rag.objects.bodies.values()))
    result = rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id="tenant_a"))
    assert document_id
    assert stored == payload
    assert "星星公司规章制度" in result.citations[0].text
    assert "公司年假7天" in result.citations[0].text
    replaced = _docx("星星公司规章制度", "公司年假10天")
    same_id = rag.ingest_file(
        tenant_id="tenant_a",
        filename="星星公司的规章制度.docx",
        body=replaced,
    )
    updated = rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id="tenant_a"))
    assert same_id == document_id
    assert next(iter(rag.objects.bodies.values())) == replaced
    assert "10天" in updated.citations[0].text
    assert "7天" not in updated.citations[0].text


def test_gateway_accepts_a_docx_upload() -> None:
    rag = build_memory_rag()
    client, _spy = _client(rag, default_funnel())
    headers = _auth(client)
    created = client.post(
        "/v1/documents",
        files={
            "file": (
                "规章制度.docx",
                _docx("查阅制度编号 HR-2024-001", "公司年假7天"),
                "application/octet-stream",
            )
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["document_id"]
    asked = client.post(
        "/v1/tasks", json={"text": "查阅制度编号 HR-2024-001"}, headers=headers
    )
    assert asked.status_code == 200
    assert "年假" in asked.json()["retrieved_context"]["citations"][0]["text"]
    rejected = client.post(
        "/v1/documents",
        files={"file": ("规章制度.doc", b"old", "application/msword")},
        headers=headers,
    )
    assert rejected.status_code == 400


def test_pdf_text_can_be_extracted() -> None:
    text = extract_text("leave.pdf", _simple_pdf("Annual leave is five days"))
    assert "Annual leave" in text


@pytest.mark.skipif(
    not postgres_configured(), reason="PostgreSQL credentials are not configured"
)
def test_ingest_stores_the_object_key_in_postgres() -> None:
    rag = _rag_with(catalog=PostgresCatalog())
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    document_id = rag.ingest_text(
        tenant_id=tenant_id, title="员工手册", text="员工年假每年五天。"
    )
    conn = connect()
    try:
        migrate(conn)
        document = conn.execute(
            "SELECT oss_key, source_uri FROM documents WHERE id = %s AND tenant_id = %s",
            (document_id, tenant_id),
        ).fetchone()
        chunk = conn.execute(
            "SELECT body FROM document_chunks WHERE document_id = %s",
            (document_id,),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    assert document["oss_key"]
    assert document["source_uri"].startswith("oss://")
    assert "年假" in chunk["body"]


def _minio_configured() -> bool:
    from fs_orchestrator.postgres import load_local_env

    load_local_env()
    names = ("MINIO_ENDPOINT", "MINIO_BUCKET", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")
    return all(os.environ.get(name) for name in names)


class _FakeMinio:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, name: str) -> bool:
        return name in self.buckets

    def make_bucket(self, name: str) -> None:
        self.buckets.add(name)

    def put_object(
        self, bucket: str, key: str, data: object, length: int, content_type: str
    ) -> None:
        del length, content_type
        self.objects[(bucket, key)] = data.read()  # type: ignore[attr-defined]


def test_minio_put_returns_an_s3_uri_and_creates_the_bucket() -> None:
    fake = _FakeMinio()
    store = MinioObjectStore(
        "127.0.0.1:9000", "fs-documents", "key", "secret", client=fake
    )
    uri = store.put("tenant_a/doc_1", "员工年假".encode(), media_type="text/plain")
    assert uri == "s3://fs-documents/tenant_a/doc_1"
    assert fake.buckets == {"fs-documents"}
    assert fake.objects[("fs-documents", "tenant_a/doc_1")] == "员工年假".encode()


def test_oss_is_chosen_when_both_object_stores_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_BUCKET", "fs-documents")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    monkeypatch.setenv("MINIO_BUCKET", "fs-documents")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "secret")
    assert isinstance(object_store_from_env(), AliyunObjectStore)


def test_minio_is_chosen_when_oss_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSS_ENDPOINT", raising=False)
    monkeypatch.delenv("OSS_BUCKET", raising=False)
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    monkeypatch.setenv("MINIO_BUCKET", "fs-documents")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "secret")
    monkeypatch.setenv("MINIO_SECURE", "false")
    assert isinstance(object_store_from_env(), MinioObjectStore)


@pytest.mark.skipif(not _minio_configured(), reason="MinIO is not configured")
def test_ingest_stores_the_original_in_minio() -> None:
    from minio import Minio

    rag = build_rag_service()
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    text = "员工年假每年五天。"
    document_id = rag.ingest_text(tenant_id=tenant_id, title="员工手册", text=text)
    endpoint = (
        os.environ["MINIO_ENDPOINT"].removeprefix("https://").removeprefix("http://")
    )
    secure = os.environ.get("MINIO_SECURE", "false").lower() in {"1", "true", "yes"}
    client = Minio(
        endpoint,
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=secure,
    )
    response = client.get_object(
        os.environ["MINIO_BUCKET"], f"{tenant_id}/{document_id}"
    )
    try:
        body = response.read()
    finally:
        response.close()
        response.release_conn()
    assert body == text.encode()


def _elasticsearch_configured() -> bool:
    from fs_orchestrator.postgres import load_local_env

    load_local_env()
    return bool(os.environ.get("ELASTICSEARCH_URL"))


class _FakeElasticsearch:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, str]] = {}
        self.created = False
        self.indices = self

    def exists(self, *, index: str) -> bool:
        del index
        return self.created

    def create(self, *, index: str, settings: dict, mappings: dict) -> None:
        del index, settings, mappings
        self.created = True

    def index(
        self, *, index: str, id: str, document: dict[str, str], refresh: bool
    ) -> None:
        del index, refresh
        self.docs[id] = document

    def search(self, *, index: str, size: int, query: dict) -> dict:
        del index
        tenant_id = query["bool"]["filter"][0]["term"]["tenant_id"]
        text = query["bool"]["must"][0]["match"]["text"]
        hits = [
            {"_source": {"chunk_id": document["chunk_id"]}}
            for document in self.docs.values()
            if document["tenant_id"] == tenant_id and text in document["text"]
        ]
        return {"hits": {"hits": hits[:size]}}

    def delete(self, *, index: str, id: str, refresh: bool) -> None:
        del index, refresh
        self.docs.pop(id, None)


def test_elasticsearch_search_returns_chunk_ids_for_the_same_tenant() -> None:
    fake = _FakeElasticsearch()
    lexical = EsLexicalIndex("http://127.0.0.1:9200", client=fake)
    lexical.index(_chunk("chunk_a", "tenant_a", "员工年假每年五天"))
    lexical.index(_chunk("chunk_b", "tenant_b", "员工年假每年五天"))
    assert lexical.search(tenant_id="tenant_a", query="年假", limit=5) == ["chunk_a"]
    lexical.delete("tenant_a", ["chunk_a"])
    assert lexical.search(tenant_id="tenant_a", query="年假", limit=5) == []
    assert fake.created is True


def test_elasticsearch_is_chosen_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200")
    assert isinstance(build_rag_service().lexical, EsLexicalIndex)


@pytest.mark.skipif(
    not _elasticsearch_configured(), reason="Elasticsearch is not configured"
)
def test_bm25_hits_come_from_elasticsearch() -> None:
    lexical = EsLexicalIndex(os.environ["ELASTICSEARCH_URL"])
    rag = _rag_with(lexical=lexical)
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    other = f"tenant_{uuid.uuid4().hex[:8]}"
    rag.ingest_text(tenant_id=tenant_id, title="员工手册", text="员工年假每年五天。")
    rag.ingest_text(tenant_id=other, title="员工手册", text="员工年假每年五天。")
    result = rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id=tenant_id))
    assert "bm25" in result.channels
    assert result.citations
    assert all(item.text for item in result.citations)
    foreign = lexical.search(tenant_id=other, query="年假", limit=5)
    own = lexical.search(tenant_id=tenant_id, query="年假", limit=5)
    assert own
    assert set(own).isdisjoint(foreign)


def _milvus_configured() -> bool:
    from fs_orchestrator.postgres import load_local_env

    load_local_env()
    return bool(os.environ.get("MILVUS_URI"))


class _FakeMilvus:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.collections: set[str] = set()

    def has_collection(self, name: str) -> bool:
        return name in self.collections

    def create_schema(self, **kwargs: object) -> object:
        del kwargs
        return self

    def add_field(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def prepare_index_params(self) -> object:
        return self

    def add_index(self, **kwargs: object) -> None:
        del kwargs

    def create_collection(self, **kwargs: object) -> None:
        self.collections.add(str(kwargs["collection_name"]))

    def insert(self, *, collection_name: str, data: list[dict[str, object]]) -> None:
        del collection_name
        self.rows.extend(data)

    def flush(self, name: str) -> None:
        del name

    def search(
        self,
        *,
        collection_name: str,
        data: list[list[float]],
        limit: int,
        filter: str,
        output_fields: list[str],
    ) -> list[list[dict[str, dict[str, str]]]]:
        del collection_name, data, output_fields
        tenant_id = filter.split('"')[1]
        hits = [
            {"entity": {"chunk_id": str(row["chunk_id"])}}
            for row in self.rows
            if row["tenant_id"] == tenant_id
        ]
        return [hits[:limit]]

    def delete(self, *, collection_name: str, filter: str) -> None:
        del collection_name
        self.rows = [row for row in self.rows if f'"{row["chunk_id"]}"' not in filter]


def test_milvus_search_keeps_tenants_apart() -> None:
    fake = _FakeMilvus()
    vectors = MilvusVectorIndex("http://127.0.0.1:19530", client=fake)
    vectors.index(_chunk("chunk_a", "tenant_a", "员工年假每年五天"), [0.1] * 32)
    vectors.index(_chunk("chunk_b", "tenant_b", "员工年假每年五天"), [0.1] * 32)
    assert vectors.search(tenant_id="tenant_a", vector=[0.1] * 32, limit=5) == [
        "chunk_a"
    ]
    vectors.delete("tenant_a", ["chunk_a"])
    assert vectors.search(tenant_id="tenant_a", vector=[0.1] * 32, limit=5) == []
    assert "fs_chunks" in fake.collections


def test_milvus_is_chosen_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MILVUS_URI", "http://127.0.0.1:19530")
    assert isinstance(build_rag_service().vectors, MilvusVectorIndex)


@pytest.mark.skipif(not _milvus_configured(), reason="Milvus is not configured")
def test_vector_hits_come_from_milvus() -> None:
    vectors = MilvusVectorIndex(os.environ["MILVUS_URI"], collection="fs_chunks_hash")
    rag = _rag_with(vectors=vectors)
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    other = f"tenant_{uuid.uuid4().hex[:8]}"
    text = "员工年假每年五天。"
    rag.ingest_text(tenant_id=tenant_id, title="员工手册", text=text)
    rag.ingest_text(tenant_id=other, title="员工手册", text=text)
    result = rag.retrieve_blocking(RetrieveRequest(query="年假", tenant_id=tenant_id))
    assert "vector" in result.channels
    own = vectors.search(tenant_id=tenant_id, vector=embed(text), limit=5)
    foreign = vectors.search(tenant_id=other, vector=embed(text), limit=5)
    assert own
    assert set(own).isdisjoint(foreign)


class _RerankResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def test_dashscope_rerank_reorders_by_the_model_result() -> None:
    def post(url: str, **kwargs: object) -> _RerankResponse:
        del url
        body = kwargs["json"]
        assert isinstance(body, dict)
        assert body["model"] == "qwen3-rerank"
        assert body["query"] == "什么是文本排序模型"
        return _RerankResponse(
            {
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.2},
                ]
            }
        )

    reranker = DashScopeReranker(
        api_key="test-key",
        workspace_id="workspace",
        model="qwen3-rerank",
        post=post,  # type: ignore[arg-type]
    )
    ranked = reranker.rerank(
        "什么是文本排序模型",
        [
            FusedHit("quantum", 0.4, ["bm25"], "量子计算是计算科学的一个前沿领域"),
            FusedHit(
                "ranker", 0.1, ["vector"], "文本排序模型广泛用于搜索引擎和推荐系统中"
            ),
        ],
    )
    assert [item.chunk_id for item in ranked] == ["ranker", "quantum"]
    assert ranked[0].score == 0.91
    assert ranked[0].channels == ["vector"]


def _rerank_configured() -> bool:
    from fs_orchestrator.postgres import load_local_env

    load_local_env()
    return bool(os.environ.get("DASHSCOPE_API_KEY") and os.environ.get("WORKSPACE_ID"))


@pytest.mark.skipif(not _rerank_configured(), reason="rerank model is not configured")
def test_live_rerank_model_prefers_the_matching_passage() -> None:
    reranker = build_rag_service().reranker
    ranked = reranker.rerank(
        "什么是文本排序模型",
        [
            FusedHit("quantum", 0.2, ["bm25"], "量子计算是计算科学的一个前沿领域"),
            FusedHit(
                "ranker",
                0.2,
                ["vector"],
                "文本排序模型广泛用于搜索引擎和推荐系统中，它们根据文本相关性对候选文本进行排序",
            ),
            FusedHit(
                "language",
                0.2,
                ["bm25"],
                "预训练语言模型的发展给文本排序模型带来了新的进展",
            ),
        ],
    )
    assert isinstance(reranker, DashScopeReranker)
    assert ranked[0].chunk_id == "ranker"
    assert ranked[-1].chunk_id == "quantum"
    assert ranked[0].score >= ranked[1].score


def test_dashscope_embedding_keeps_input_order() -> None:
    def post(url: str, **kwargs: object) -> _RerankResponse:
        del url
        body = kwargs["json"]
        assert isinstance(body, dict)
        assert body["model"] == "qwen3.7-text-embedding"
        assert body["dimensions"] == 2
        return _RerankResponse(
            {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            }
        )

    embedder = DashScopeEmbedder(
        api_key="test-key",
        workspace_id="workspace",
        model="qwen3.7-text-embedding",
        dimensions=2,
        post=post,  # type: ignore[arg-type]
    )
    assert embedder.embed_many(["年假", "量子"]) == [[1.0, 0.0], [0.0, 1.0]]


def _embedding_configured() -> bool:
    from fs_orchestrator.postgres import load_local_env

    load_local_env()
    return bool(
        os.environ.get("DASHSCOPE_API_KEY")
        and os.environ.get("WORKSPACE_ID")
        and os.environ.get("EMBEDDING_MODEL")
    )


@pytest.mark.skipif(
    not _embedding_configured(), reason="embedding model is not configured"
)
def test_live_embedding_puts_related_text_closer() -> None:
    embedder = build_rag_service().embedder
    annual = embedder.embed("员工每年可以休五天年假")
    leave = embedder.embed("公司规定的带薪休假天数")
    quantum = embedder.embed("量子计算是计算科学的一个前沿领域")
    assert isinstance(embedder, DashScopeEmbedder)
    assert len(annual) == embedder.dim
    assert cosine(annual, leave) > cosine(annual, quantum)


@pytest.mark.skipif(
    not (_embedding_configured() and _milvus_configured()),
    reason="embedding model or Milvus is not configured",
)
def test_live_embedding_search_returns_the_related_chunk() -> None:
    rag = build_rag_service()
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    rag.ingest_text(
        tenant_id=tenant_id,
        title="假期",
        text="员工每年可以休五天年假，申请需要提前三个工作日。",
    )
    rag.ingest_text(
        tenant_id=tenant_id, title="量子", text="量子计算是计算科学的一个前沿领域。"
    )
    hits = rag.vectors.search(
        tenant_id=tenant_id,
        vector=rag.embedder.embed("带薪休假的申请规则"),
        limit=2,
    )
    chunk = rag.catalog.get_chunk(hits[0], tenant_id=tenant_id)
    assert chunk is not None
    assert "年假" in chunk.text


def _chunk(chunk_id: str, tenant_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id="doc_1",
        tenant_id=tenant_id,
        text=text,
        ordinal=0,
        source_uri="oss://local/doc_1",
    )


def _rag_with(**overrides: object) -> RagService:
    parts: dict[str, object] = {
        "objects": MemoryObjectStore(),
        "catalog": MemoryCatalog(),
        "lexical": MemoryLexicalIndex(),
        "vectors": MemoryVectorIndex(),
        "graph": MemoryGraph(),
        "cache": MemoryQueryCache(),
    }
    parts.update(overrides)
    return RagService(**parts)  # type: ignore[arg-type]


def _client(
    rag: RagService, funnel: IntentFunnel, rag_chat: object | None = None
) -> tuple[TestClient, SpyOrchestrator]:
    settings = GatewaySettings(
        jwt_secret="test-secret-must-be-at-least-32-bytes",
        password_iterations=1000,
        cors_origins="http://localhost:8000",
    )
    state = GatewayState.create(settings)
    state.users.add(
        username="ada",
        password="secret",
        user_id="user_ada",
        tenant_id="tenant_a",
        scopes=["tasks:write"],
    )
    spy = SpyOrchestrator()
    client = TestClient(
        create_app(
            settings,
            state,
            spy,
            funnel,
            MemoryDecisionLog(),
            RetrievingKnowledge(rag),
            rag,
            rag_chat,
        )
    )
    return client, spy


class _QuotaChat:
    def __init__(self, model: str) -> None:
        self.model = model

    async def acomplete(
        self, messages: list[dict[str, str]], *, enable_thinking: bool | None = None
    ) -> str:
        del messages, enable_thinking
        raise ModelQuotaExhausted(self.model)


class _RecordingChat:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.messages: list[dict[str, str]] = []
        self.fail = False

    async def acomplete(
        self, messages: list[dict[str, str]], *, enable_thinking: bool | None = None
    ) -> str:
        del enable_thinking
        self.messages = messages
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.answer


def _docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _simple_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 40 100 Td ({text}) Tj ET\n".encode()
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        ),
        (
            f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"endstream\nendobj\n"
        ),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    body = b"".join(objects)
    offsets = [0]
    cursor = len(header)
    for obj in objects:
        offsets.append(cursor)
        cursor += len(obj)
    xref = [f"xref\n0 {len(offsets)}\n".encode(), b"0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    trailer = (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{len(header) + len(body)}\n%%EOF\n"
    ).encode()
    return header + body + b"".join(xref) + trailer


def _auth(client: TestClient) -> dict[str, str]:
    token = client.post(
        "/v1/auth/login", json={"username": "ada", "password": "secret"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_regex_layer_is_what_the_gateway_used() -> None:
    assert IntentLayer.REGEX.value == "regex"
