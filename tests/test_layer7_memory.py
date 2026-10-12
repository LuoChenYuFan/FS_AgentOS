"""Layer 7: a new session reads long-term facts, and resume does not replay nodes."""

import json
import uuid

import pytest
from fs_memory_sdk import MemoryRecord, MemoryScope
from fs_memory_service import MemoryService, PostgresMemoryStore
from fs_orchestrator.ledger import DurableRuns
from fs_orchestrator.postgres import connect, migrate, postgres_configured

pytestmark = pytest.mark.skipif(not postgres_configured(), reason="PostgreSQL credentials are not configured")


class DictStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, MemoryScope, str], MemoryRecord] = {}
        self.fetches = 0
        self.lists = 0

    def upsert(self, record: MemoryRecord) -> None:
        self.rows[(record.tenant_id, record.user_id, record.scope, record.key)] = record

    def fetch(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> MemoryRecord | None:
        self.fetches += 1
        return self.rows.get((tenant_id, user_id, scope, key))

    def list_long_term(self, *, tenant_id: str, user_id: str) -> list[MemoryRecord]:
        self.lists += 1
        return [
            record
            for record in self.rows.values()
            if record.tenant_id == tenant_id
            and record.user_id == user_id
            and record.scope is MemoryScope.LONG_TERM
        ]

    def delete(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> None:
        self.rows.pop((tenant_id, user_id, scope, key), None)


class DictCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        del ttl_seconds
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def _migrated() -> None:
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"tenant_{suffix}", f"user_{suffix}"


def _state(run_id: str) -> dict:
    conn = connect()
    try:
        migrate(conn)
        row = conn.execute("SELECT state_json FROM runs WHERE id = %s", (run_id,)).fetchone()
        conn.commit()
    finally:
        conn.close()
    assert row is not None
    state = row["state_json"]
    assert isinstance(state, dict)
    return state


def test_redis_cache_answers_without_reading_the_store_again() -> None:
    store = DictStore()
    service = MemoryService(store=store, cache=DictCache())
    service.remember(
        MemoryRecord(
            scope=MemoryScope.LONG_TERM,
            key="home_city",
            content={"city": "北京"},
            tenant_id="tenant_cache",
            user_id="user_cache",
        )
    )
    store.rows.clear()
    found = service.recall(
        MemoryScope.LONG_TERM,
        "home_city",
        tenant_id="tenant_cache",
        user_id="user_cache",
    )
    assert found is not None
    assert found.content == {"city": "北京"}
    assert store.fetches == 0

    service.remember(
        MemoryRecord(
            scope=MemoryScope.LONG_TERM,
            key="home_city",
            content={"city": "北京"},
            tenant_id="tenant_cache",
            user_id="user_cache",
        )
    )
    assert service.recall_long_term(tenant_id="tenant_cache", user_id="user_cache") == [
        {"key": "home_city", "content": {"city": "北京"}}
    ]
    assert store.lists == 1
    store.rows.clear()
    assert service.recall_long_term(tenant_id="tenant_cache", user_id="user_cache") == [
        {"key": "home_city", "content": {"city": "北京"}}
    ]
    assert store.lists == 1


def test_a_new_session_reads_a_long_term_fact() -> None:
    _migrated()
    tenant_id, user_id = _ids()
    memory = MemoryService(store=PostgresMemoryStore(connect))
    memory.remember(
        MemoryRecord(
            scope=MemoryScope.LONG_TERM,
            key="home_city",
            content={"city": "北京"},
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
    first = f"run_{uuid.uuid4().hex}"
    second = f"run_{uuid.uuid4().hex}"
    runs = DurableRuns(memory=memory)
    runs.run(
        run_id=first,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="帮我写一封邮件",
        idempotency_key=f"idem_{first}",
        session_id=f"sess_{first}",
        max_steps=1,
    )
    runs.run(
        run_id=second,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="明天去哪",
        idempotency_key=f"idem_{second}",
        session_id=f"sess_{second}",
        max_steps=1,
    )
    assert _state(first)["long_term"] == [{"key": "home_city", "content": {"city": "北京"}}]
    assert _state(second)["long_term"] == [{"key": "home_city", "content": {"city": "北京"}}]
    messages = _state(second)["messages"]
    assert messages == [{"role": "user", "content": "明天去哪"}]
    assert "visits" not in json.dumps(messages, ensure_ascii=False)


def test_resume_does_not_replay_finished_nodes() -> None:
    _migrated()
    tenant_id, user_id = _ids()
    memory = MemoryService(store=PostgresMemoryStore(connect))
    run_id = f"run_{uuid.uuid4().hex}"
    started = DurableRuns(memory=memory).run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="帮我写一封邮件",
        idempotency_key=f"idem_{run_id}",
        session_id=f"sess_{run_id}",
        max_steps=1,
    )
    assert started["current_node"] == "collect"
    assert _state(run_id)["task_results"]["collect"]["visits"] == 1

    memory.remember(
        MemoryRecord(
            scope=MemoryScope.LONG_TERM,
            key="mail_to",
            content={"name": "老张"},
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
    continued = DurableRuns(memory=memory).resume(run_id, tenant_id=tenant_id)
    assert continued["current_node"] == "draft"
    mid = _state(run_id)
    assert mid["task_results"]["collect"]["visits"] == 1
    assert mid["completed"] == ["collect", "draft"]
    assert {"key": "mail_to", "content": {"name": "老张"}} in mid["long_term"]

    finished = DurableRuns(memory=memory).resume(run_id, tenant_id=tenant_id)
    assert finished["status"] == "done"
    assert finished["current_node"] == "join"
    done = _state(run_id)
    assert done["task_results"]["collect"]["visits"] == 1
    assert done["completed"] == ["collect", "draft", "join"]
    assert done["messages"] == [
        {"role": "user", "content": "帮我写一封邮件"},
        {"role": "assistant", "content": "帮我写一封邮件"},
    ]
    assert "visits" not in json.dumps(done["messages"], ensure_ascii=False)
    assert "task_results" not in json.dumps(done["messages"], ensure_ascii=False)
