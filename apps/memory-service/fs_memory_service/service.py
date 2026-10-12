"""Facts in PostgreSQL. Reads try Redis first.

Long-term facts stay until they are overwritten. Session transcript and working
slots use a shorter cache lifetime; the dialogue board owns idle expiry.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fs_common import new_id
from fs_memory_sdk import MemoryRecord, MemoryScope

_CACHE_TTL = {
    MemoryScope.SESSION: 6 * 3600,
    MemoryScope.WORKING: 6 * 3600,
    MemoryScope.LONG_TERM: 7 * 24 * 3600,
}


class MemoryStore(Protocol):
    def upsert(self, record: MemoryRecord) -> None: ...

    def fetch(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> MemoryRecord | None: ...

    def list_long_term(self, *, tenant_id: str, user_id: str) -> list[MemoryRecord]: ...

    def delete(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> None: ...


class MemoryCache(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None: ...

    def delete(self, key: str) -> None: ...


class PostgresMemoryStore:
    """One connection per call. The caller owns migrate()."""

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def upsert(self, record: MemoryRecord) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO memory_records (
                    id, tenant_id, user_id, scope, memory_key, content, token_estimate
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, user_id, scope, memory_key)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    token_estimate = EXCLUDED.token_estimate,
                    updated_at = now()
                """,
                (
                    new_id("mem"),
                    record.tenant_id,
                    record.user_id,
                    record.scope.value,
                    record.key,
                    json.dumps(record.content, ensure_ascii=False),
                    record.token_estimate,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def fetch(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> MemoryRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT scope, memory_key, content, token_estimate, tenant_id, user_id
                FROM memory_records
                WHERE tenant_id = %s AND user_id = %s AND scope = %s AND memory_key = %s
                """,
                (tenant_id, user_id, scope.value, key),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
        if row is None:
            return None
        return _record(row)

    def list_long_term(self, *, tenant_id: str, user_id: str) -> list[MemoryRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT scope, memory_key, content, token_estimate, tenant_id, user_id
                FROM memory_records
                WHERE tenant_id = %s AND user_id = %s AND scope = %s
                ORDER BY memory_key
                """,
                (tenant_id, user_id, MemoryScope.LONG_TERM.value),
            ).fetchall()
            conn.commit()
        finally:
            conn.close()
        return [_record(row) for row in rows]

    def delete(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                DELETE FROM memory_records
                WHERE tenant_id = %s AND user_id = %s AND scope = %s AND memory_key = %s
                """,
                (tenant_id, user_id, scope.value, key),
            )
            conn.commit()
        finally:
            conn.close()


class RedisMemoryCache:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> str | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._client.set(key, value, ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(key)


class NullCache:
    def get(self, key: str) -> str | None:
        del key
        return None

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        del key, value, ttl_seconds

    def delete(self, key: str) -> None:
        del key


class MemoryService:
    def __init__(self, store: MemoryStore, cache: MemoryCache | None = None) -> None:
        self._store = store
        self._cache_override = cache
        self._env_cache: MemoryCache | None = None

    def _cache_client(self) -> MemoryCache:
        if self._cache_override is not None:
            return self._cache_override
        if self._env_cache is None:
            _load_local_env()
            self._env_cache = redis_cache_from_env()
        return self._env_cache

    async def put(self, record: MemoryRecord) -> None:
        self.remember(record)

    async def get(
        self,
        scope: MemoryScope,
        key: str,
        *,
        tenant_id: str = "",
        user_id: str = "",
    ) -> MemoryRecord | None:
        return self.recall(scope, key, tenant_id=tenant_id, user_id=user_id)

    def remember(self, record: MemoryRecord) -> None:
        self._store.upsert(record)
        item = _item_key(record.tenant_id, record.user_id, record.scope, record.key)
        self._drop(item)
        self._drop(_list_key(record.tenant_id, record.user_id))
        self._save(item, record.model_dump_json(), record.scope)

    def forget(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> None:
        self._store.delete(tenant_id=tenant_id, user_id=user_id, scope=scope, key=key)
        self._drop(_item_key(tenant_id, user_id, scope, key))
        self._drop(_list_key(tenant_id, user_id))

    def recall(
        self,
        scope: MemoryScope,
        key: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> MemoryRecord | None:
        item = _item_key(tenant_id, user_id, scope, key)
        cached = self._load(item)
        if cached:
            return MemoryRecord.model_validate_json(cached)
        found = self._store.fetch(tenant_id=tenant_id, user_id=user_id, scope=scope, key=key)
        if found is not None:
            self._save(item, found.model_dump_json(), scope)
        return found

    def recall_long_term(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        cache_key = _list_key(tenant_id, user_id)
        cached = self._load(cache_key)
        if cached:
            payload = json.loads(cached)
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
        records = self._store.list_long_term(tenant_id=tenant_id, user_id=user_id)
        facts = [{"key": record.key, "content": record.content} for record in records]
        self._save(cache_key, json.dumps(facts, ensure_ascii=False), MemoryScope.LONG_TERM)
        return facts

    def _load(self, key: str) -> str | None:
        try:
            return self._cache_client().get(key)
        except _cache_errors():
            return None

    def _save(self, key: str, value: str, scope: MemoryScope) -> None:
        try:
            self._cache_client().put(key, value, ttl_seconds=_CACHE_TTL[scope])
        except _cache_errors():
            return

    def _drop(self, key: str) -> None:
        try:
            self._cache_client().delete(key)
        except _cache_errors():
            return


def _load_local_env() -> None:
    path = Path(__file__).resolve().parents[3] / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def redis_cache_from_env() -> MemoryCache:
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return NullCache()
    try:
        import redis
    except ImportError:
        return NullCache()
    return RedisMemoryCache(
        redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    )


def _record(row: dict[str, Any]) -> MemoryRecord:
    content = row["content"]
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict):
        content = {}
    return MemoryRecord(
        scope=MemoryScope(row["scope"]),
        key=str(row["memory_key"]),
        content=content,
        token_estimate=int(row["token_estimate"] or 0),
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
    )


def _item_key(tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> str:
    return f"mem:{tenant_id}:{user_id}:{scope.value}:{key}"


def _list_key(tenant_id: str, user_id: str) -> str:
    return f"mem:{tenant_id}:{user_id}:long_term:all"


def _cache_errors() -> tuple[type[BaseException], ...]:
    try:
        import redis
    except ImportError:
        return (OSError, TypeError, ValueError)
    return (redis.RedisError, OSError, TypeError, ValueError)
