"""External backends. Constructed only when their address is in the environment."""

from __future__ import annotations

from fs_rag_service.errors import RetrievalBackendError
from fs_rag_service.memory import Chunk
from fs_rag_service.textutil import embed


class RedisQueryCache:
    def __init__(self, url: str) -> None:
        try:
            import redis
        except ImportError as exc:
            raise RetrievalBackendError("redis is not installed") from exc
        self._client = redis.Redis.from_url(url)

    def get(self, key: str) -> str | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._client.set(key, value, ex=ttl_seconds)

    def drop_tenant(self, tenant_id: str, index_version: str) -> None:
        pattern = f"rag:{index_version}:{tenant_id}:*"
        for key in self._client.scan_iter(match=pattern):
            self._client.delete(key)


class EsLexicalIndex:
    """BM25 over chunk text. Chinese is matched with overlapping bigrams."""

    def __init__(self, url: str, *, client: object | None = None) -> None:
        self._index = "fs_chunks"
        self._ready = False
        if client is not None:
            self._client = client
            return
        try:
            from elasticsearch import Elasticsearch
        except ImportError as exc:
            raise RetrievalBackendError("elasticsearch is not installed") from exc
        self._client = Elasticsearch(url)

    def index(self, chunk: Chunk) -> None:
        self._ensure()
        try:
            self._client.index(
                index=self._index,
                id=chunk.chunk_id,
                document={
                    "tenant_id": chunk.tenant_id,
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                },
                refresh=False,
            )
        except _es_errors() as exc:
            raise RetrievalBackendError("elasticsearch index failed") from exc

    def flush(self) -> None:
        """Make buffered chunk indexes searchable once per document."""
        self._ensure()
        try:
            self._client.indices.refresh(index=self._index)
        except _es_errors() as exc:
            raise RetrievalBackendError("elasticsearch refresh failed") from exc

    def delete(self, tenant_id: str, chunk_ids: list[str]) -> None:
        del tenant_id
        if not chunk_ids:
            return
        self._ensure()
        for chunk_id in chunk_ids:
            try:
                self._client.delete(index=self._index, id=chunk_id, refresh=True)
            except _es_errors() as exc:
                if (
                    getattr(exc, "status_code", None) == 404
                    or "not_found" in str(exc).lower()
                ):
                    continue
                raise RetrievalBackendError("elasticsearch delete failed") from exc

    def search(self, *, tenant_id: str, query: str, limit: int) -> list[str]:
        self._ensure()
        try:
            response = self._client.search(
                index=self._index,
                size=limit,
                query={
                    "bool": {
                        "must": [{"match": {"text": query}}],
                        "filter": [{"term": {"tenant_id": tenant_id}}],
                    }
                },
            )
        except _es_errors() as exc:
            raise RetrievalBackendError("elasticsearch search failed") from exc
        body = getattr(response, "body", response)
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
        return [
            hit["_source"]["chunk_id"]
            for hit in hits
            if hit.get("_source", {}).get("chunk_id")
        ]

    def _ensure(self) -> None:
        if self._ready:
            return
        try:
            exists = self._client.indices.exists(index=self._index)
            if not exists:
                self._client.indices.create(
                    index=self._index, settings=_ES_SETTINGS, mappings=_ES_MAPPINGS
                )
        except _es_errors() as exc:
            if "resource_already_exists_exception" not in str(exc):
                raise RetrievalBackendError("elasticsearch index setup failed") from exc
        self._ready = True


_ES_SETTINGS = {
    "analysis": {
        "analyzer": {
            "fs_cjk": {
                "tokenizer": "standard",
                "filter": ["cjk_width", "lowercase", "cjk_bigram"],
            }
        }
    }
}
_ES_MAPPINGS = {
    "properties": {
        "tenant_id": {"type": "keyword"},
        "chunk_id": {"type": "keyword"},
        "text": {"type": "text", "analyzer": "fs_cjk"},
    }
}


def _es_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        LookupError,
    ]
    try:
        from elastic_transport import ConnectionError as TransportConnectionError
        from elasticsearch import ApiError
    except ImportError:
        return tuple(errors)
    errors.extend([ApiError, TransportConnectionError])
    return tuple(errors)


class MilvusVectorIndex:
    def __init__(
        self,
        uri: str,
        *,
        dim: int = 32,
        collection: str = "fs_chunks",
        client: object | None = None,
    ) -> None:
        self._name = collection
        self._dim = dim
        self._uri = uri
        self._client = client
        self._ready = False

    def index(self, chunk: Chunk, vector: list[float] | None = None) -> None:
        self._ensure()
        stored = vector if vector is not None else embed(chunk.text, self._dim)
        try:
            self._client.insert(
                collection_name=self._name,
                data=[
                    {
                        "chunk_id": chunk.chunk_id,
                        "tenant_id": chunk.tenant_id,
                        "vector": stored,
                    }
                ],
            )
        except _milvus_errors() as exc:
            raise RetrievalBackendError("milvus index failed") from exc

    def flush(self) -> None:
        """Persist buffered inserts once per document, not after every chunk."""
        self._ensure()
        try:
            self._client.flush(self._name)
        except _milvus_errors() as exc:
            raise RetrievalBackendError("milvus flush failed") from exc

    def delete(self, tenant_id: str, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        if _unsafe(tenant_id) or any(_unsafe(chunk_id) for chunk_id in chunk_ids):
            raise RetrievalBackendError("tenant_id is not a safe filter value")
        self._ensure()
        quoted = ", ".join(f'"{chunk_id}"' for chunk_id in chunk_ids)
        try:
            self._client.delete(
                collection_name=self._name,
                filter=f'chunk_id in [{quoted}] and tenant_id == "{tenant_id}"',
            )
        except _milvus_errors() as exc:
            raise RetrievalBackendError("milvus delete failed") from exc

    def search(self, *, tenant_id: str, vector: list[float], limit: int) -> list[str]:
        if _unsafe(tenant_id):
            raise RetrievalBackendError("tenant_id is not a safe filter value")
        self._ensure()
        try:
            groups = self._client.search(
                collection_name=self._name,
                data=[vector],
                limit=limit,
                filter=f'tenant_id == "{tenant_id}"',
                output_fields=["chunk_id"],
            )
        except _milvus_errors() as exc:
            raise RetrievalBackendError("milvus search failed") from exc
        first = groups[0] if groups else []
        found: list[str] = []
        for hit in first:
            chunk_id = _milvus_chunk_id(hit)
            if chunk_id:
                found.append(chunk_id)
        return found

    def _ensure(self) -> None:
        if self._ready:
            return
        if self._client is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:
                raise RetrievalBackendError("pymilvus is not installed") from exc
            self._client = MilvusClient(uri=self._uri)
        try:
            existing = self._existing_dim()
            if existing is not None and existing != self._dim:
                self._client.drop_collection(self._name)
                existing = None
            if existing is None:
                from pymilvus import DataType

                schema = self._client.create_schema(
                    auto_id=False, enable_dynamic_field=False
                )
                schema.add_field(
                    "chunk_id", DataType.VARCHAR, is_primary=True, max_length=64
                )
                schema.add_field("tenant_id", DataType.VARCHAR, max_length=128)
                schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self._dim)
                index_params = self._client.prepare_index_params()
                index_params.add_index(field_name="vector", metric_type="COSINE")
                self._client.create_collection(
                    collection_name=self._name,
                    schema=schema,
                    index_params=index_params,
                )
        except _milvus_errors() as exc:
            raise RetrievalBackendError("milvus collection setup failed") from exc
        self._ready = True

    def _existing_dim(self) -> int | None:
        if not self._client.has_collection(self._name):
            return None
        description = self._client.describe_collection(self._name)
        fields = description.get("fields", []) if isinstance(description, dict) else []
        for field in fields:
            if not isinstance(field, dict) or field.get("name") != "vector":
                continue
            params = (
                field.get("params") if isinstance(field.get("params"), dict) else {}
            )
            type_params = (
                field.get("type_params")
                if isinstance(field.get("type_params"), dict)
                else {}
            )
            raw = params.get("dim", type_params.get("dim"))
            if raw is None:
                raise RetrievalBackendError("milvus vector field has no dimension")
            return int(raw)
        raise RetrievalBackendError("milvus collection has no vector field")


class Neo4jGraph:
    def __init__(self, uri: str, user: str, password: str) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RetrievalBackendError("neo4j is not installed") from exc
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def mention(self, tenant_id: str, chunk_id: str, entity_id: str) -> None:
        self._run(
            """
            MERGE (c:Chunk {id: $chunk_id, tenant_id: $tenant_id})
            MERGE (e:Entity {id: $entity_id, tenant_id: $tenant_id})
            MERGE (c)-[:MENTIONS]->(e)
            """,
            tenant_id=tenant_id,
            chunk_id=chunk_id,
            entity_id=entity_id,
        )

    def relate(self, tenant_id: str, source: str, relation: str, target: str) -> None:
        self._run(
            """
            MERGE (a:Entity {id: $source, tenant_id: $tenant_id})
            MERGE (b:Entity {id: $target, tenant_id: $tenant_id})
            MERGE (a)-[:RELATED {kind: $relation}]->(b)
            """,
            tenant_id=tenant_id,
            source=source,
            relation=relation,
            target=target,
        )

    def expand(self, chunk_ids: list[str], *, tenant_id: str, limit: int) -> list[str]:
        if not chunk_ids:
            return []
        rows = self._run(
            """
            MATCH (c:Chunk {tenant_id: $tenant_id})-[:MENTIONS]->(e:Entity)
            WHERE c.id IN $chunk_ids
            OPTIONAL MATCH (e)-[:RELATED]->(other:Entity {tenant_id: $tenant_id})
            WITH collect(DISTINCT e) + collect(DISTINCT other) AS entities
            UNWIND entities AS entity
            MATCH (found:Chunk {tenant_id: $tenant_id})-[:MENTIONS]->(entity)
            WHERE NOT found.id IN $chunk_ids
            RETURN DISTINCT found.id AS chunk_id
            LIMIT $limit
            """,
            tenant_id=tenant_id,
            chunk_ids=chunk_ids,
            limit=limit,
        )
        return [row["chunk_id"] for row in rows]

    def drop(self, tenant_id: str, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self._run(
            """
            MATCH (c:Chunk {tenant_id: $tenant_id})
            WHERE c.id IN $chunk_ids
            DETACH DELETE c
            """,
            tenant_id=tenant_id,
            chunk_ids=chunk_ids,
        )

    def _run(self, statement: str, **params: object) -> list[dict]:
        with self._driver.session() as session:
            result = session.run(statement, **params)
            return [record.data() for record in result]


class AliyunObjectStore:
    """Aliyun OSS. Used when OSS_ENDPOINT and OSS_BUCKET are set."""

    def __init__(
        self, endpoint: str, bucket_name: str, key_id: str, secret: str
    ) -> None:
        try:
            import oss2
        except ImportError as exc:
            raise RetrievalBackendError("oss2 is not installed") from exc
        auth = oss2.Auth(key_id, secret)
        self._bucket_name = bucket_name
        self._bucket = oss2.Bucket(auth, endpoint, bucket_name)

    def put(self, key: str, body: bytes, *, media_type: str) -> str:
        del media_type
        self._bucket.put_object(key, body)
        return f"oss://{self._bucket_name}/{key}"

    def delete(self, key: str) -> None:
        try:
            self._bucket.delete_object(key)
        except (OSError, RuntimeError, ValueError, TypeError, LookupError) as exc:
            raise RetrievalBackendError("oss delete failed") from exc


class MinioObjectStore:
    """S3-compatible store. Stands in for Aliyun OSS until that bucket is configured."""

    def __init__(
        self,
        endpoint: str,
        bucket_name: str,
        access_key: str,
        secret: str,
        *,
        secure: bool = False,
        client: object | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        if client is not None:
            self._client = client
            return
        try:
            from minio import Minio
        except ImportError as exc:
            raise RetrievalBackendError("minio is not installed") from exc
        host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client = Minio(
            host, access_key=access_key, secret_key=secret, secure=secure
        )

    def put(self, key: str, body: bytes, *, media_type: str) -> str:
        from io import BytesIO

        try:
            from minio.error import S3Error
        except ImportError:
            S3Error = OSError
        try:
            if not self._client.bucket_exists(self._bucket_name):
                self._client.make_bucket(self._bucket_name)
            self._client.put_object(
                self._bucket_name,
                key,
                BytesIO(body),
                length=len(body),
                content_type=media_type,
            )
        except (S3Error, OSError, RuntimeError, ValueError, TypeError) as exc:
            raise RetrievalBackendError("minio put failed") from exc
        return f"s3://{self._bucket_name}/{key}"

    def delete(self, key: str) -> None:
        try:
            from minio.error import S3Error
        except ImportError:
            S3Error = OSError
        try:
            self._client.remove_object(self._bucket_name, key)
        except S3Error as exc:
            if getattr(exc, "code", "") in {"NoSuchKey", "NoSuchBucket"}:
                return
            raise RetrievalBackendError("minio delete failed") from exc
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise RetrievalBackendError("minio delete failed") from exc


def _unsafe(value: str) -> bool:
    return not value or any(char in value for char in '"\\')


def _milvus_chunk_id(hit: object) -> str | None:
    if isinstance(hit, dict):
        entity = hit.get("entity")
        if isinstance(entity, dict) and entity.get("chunk_id"):
            return str(entity["chunk_id"])
        if hit.get("chunk_id"):
            return str(hit["chunk_id"])
        if hit.get("id"):
            return str(hit["id"])
        return None
    entity = getattr(hit, "entity", None)
    if isinstance(entity, dict) and entity.get("chunk_id"):
        return str(entity["chunk_id"])
    hit_id = getattr(hit, "id", None)
    if isinstance(hit_id, str) and hit_id:
        return hit_id
    return None


def _milvus_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        LookupError,
        KeyError,
    ]
    try:
        from pymilvus.exceptions import MilvusException
    except ImportError:
        return tuple(errors)
    errors.append(MilvusException)
    return tuple(errors)
