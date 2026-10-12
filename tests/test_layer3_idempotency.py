"""Layer 3: a side effect survives a crash and a client replay without running twice."""

import uuid

import pytest
from fs_orchestrator.ledger import DurabilityError, DurableRuns
from fs_orchestrator.postgres import connect, migrate, postgres_configured
from fs_skill_runtime import (
    EffectError,
    EffectKind,
    MemoryIdempotencyStore,
    SkillRuntime,
)
from fs_skill_sdk import SkillSpec

pytestmark_db = pytest.mark.skipif(not postgres_configured(), reason="PostgreSQL credentials are not configured")


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"tenant_{suffix}", f"user_{suffix}"


def _spec(name: str, *, safe_retry: bool, idempotency_required: bool = False) -> SkillSpec:
    return SkillSpec(name=name, safe_retry=safe_retry, idempotency_required=idempotency_required)


def test_retry_covers_only_timeout_and_server_errors() -> None:
    calls = {"n": 0}

    def boom(kind: EffectKind):
        def handler(_arguments: dict) -> str:
            calls["n"] += 1
            raise EffectError("downstream failed", kind=kind)

        return handler

    calls["n"] = 0
    runtime = SkillRuntime()
    runtime.register(_spec("lookup", safe_retry=True))
    runtime.register_handler("lookup", boom(EffectKind.SERVER))
    failed = runtime.apply("lookup", {}, idempotency_key="k", tenant_id="t", run_id="r")
    assert failed.disposition == "failed"
    assert calls["n"] == 3

    calls["n"] = 0
    runtime.register(_spec("once", safe_retry=False))
    runtime.register_handler("once", boom(EffectKind.SERVER))
    runtime.apply("once", {}, idempotency_key="k", tenant_id="t", run_id="r")
    assert calls["n"] == 1

    calls["n"] = 0
    runtime.register(_spec("client", safe_retry=True))
    runtime.register_handler("client", boom(EffectKind.CLIENT))
    client = runtime.apply("client", {}, idempotency_key="k", tenant_id="t", run_id="r")
    assert client.disposition == "failed"
    assert client.error_code == "client"
    assert calls["n"] == 1

    calls["n"] = 0
    runtime.register(_spec("auth", safe_retry=True))
    runtime.register_handler("auth", boom(EffectKind.AUTH))
    auth = runtime.apply("auth", {}, idempotency_key="k", tenant_id="t", run_id="r")
    assert auth.error_code == "auth"
    assert calls["n"] == 1


def test_action_key_returns_the_receipt_and_does_not_call_again() -> None:
    store = MemoryIdempotencyStore()
    calls = {"n": 0}

    def send(_arguments: dict) -> str:
        calls["n"] += 1
        return "rcpt-1"

    runtime = SkillRuntime(store=store)
    runtime.register(_spec("send_email", safe_retry=False, idempotency_required=True))
    runtime.register_handler("send_email", send)
    first = runtime.apply(
        "send_email",
        {},
        idempotency_key="action-1",
        tenant_id="t",
        run_id="run-1",
        approved=True,
    )
    second = runtime.apply(
        "send_email",
        {},
        idempotency_key="action-1",
        tenant_id="t",
        run_id="run-1",
        approved=True,
    )
    assert first.disposition == "sent"
    assert second.disposition == "replayed"
    assert second.result_ref == "rcpt-1"
    assert calls["n"] == 1


def test_lost_receipt_is_unknown_and_a_later_call_does_not_retry() -> None:
    store = MemoryIdempotencyStore()
    calls = {"n": 0}

    def send(_arguments: dict) -> str:
        calls["n"] += 1
        raise EffectError("smtp accepted nothing we can see", kind=EffectKind.NO_RECEIPT)

    runtime = SkillRuntime(store=store)
    spec = _spec("send_email", safe_retry=True, idempotency_required=True)
    spec = spec.model_copy(update={"requires_approval": True})
    runtime.register(spec)
    runtime.register_handler("send_email", send)
    blocked = runtime.apply("send_email", {}, idempotency_key="action-1", tenant_id="t", run_id="run-1")
    assert blocked.disposition == "escalate"
    assert calls["n"] == 0

    first = runtime.apply(
        "send_email",
        {},
        idempotency_key="action-1",
        tenant_id="t",
        run_id="run-1",
        approved=True,
    )
    second = runtime.apply(
        "send_email",
        {},
        idempotency_key="action-1",
        tenant_id="t",
        run_id="run-1",
        approved=True,
    )
    assert first.disposition == "unknown"
    assert second.disposition == "unknown"
    assert calls["n"] == 1
    assert store.rows[("t", "action-1")]["status"] == "unknown"


def test_leftover_in_progress_is_not_sent_again() -> None:
    store = MemoryIdempotencyStore()
    store.claim(key="action-1", tenant_id="t", run_id="run-1", action_type="send_email")
    calls = {"n": 0}

    def send(_arguments: dict) -> str:
        calls["n"] += 1
        return "rcpt"

    runtime = SkillRuntime(store=store)
    runtime.register(_spec("send_email", safe_retry=False, idempotency_required=True))
    runtime.register_handler("send_email", send)
    outcome = runtime.apply(
        "send_email",
        {},
        idempotency_key="action-1",
        tenant_id="t",
        run_id="run-1",
        approved=True,
    )
    assert outcome.disposition == "unknown"
    assert calls["n"] == 0
    assert store.rows[("t", "action-1")]["status"] == "unknown"


@pytestmark_db
def test_crash_after_send_and_client_replay_send_once() -> None:
    tenant_id, user_id = _ids()
    run_id = f"run_{uuid.uuid4().hex}"
    entry_key = f"idem_{run_id}"
    sent: list[str] = []

    def sender(arguments: dict) -> str:
        sent.append(str(arguments["idempotency_key"]))
        return "rcpt-1"

    def killed(_outcome: object) -> None:
        raise RuntimeError("process killed after the receipt was stored")

    started = DurableRuns().run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="给张三发邮件",
        idempotency_key=entry_key,
    )
    assert started["status"] == "awaiting_approval"
    assert started["current_node"] == "draft"
    assert sent == []

    approval, run_key = _approval(run_id)
    assert approval["status"] == "pending"
    assert approval["idempotency_key"] != run_key
    assert _effect_row(tenant_id, approval["idempotency_key"]) is None

    with pytest.raises(RuntimeError, match="process killed"):
        DurableRuns(sender=sender, after_effect=killed).resume(run_id, tenant_id=tenant_id)

    assert sent == [approval["idempotency_key"]]
    effect = _effect_row(tenant_id, approval["idempotency_key"])
    assert effect["status"] == "completed"
    assert effect["result_ref"] == "rcpt-1"
    assert "join" not in _completed(run_id)

    replay = DurableRuns(sender=sender).run(
        run_id=f"run_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="给张三发邮件",
        idempotency_key=entry_key,
    )
    assert replay["run_id"] == run_id
    assert sent == [approval["idempotency_key"]]

    with pytest.raises(DurabilityError) as conflict:
        DurableRuns().run(
            run_id=f"run_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            user_id=user_id,
            user_input="另一封",
            idempotency_key=entry_key,
        )
    assert conflict.value.code == "conflict"

    finished = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    assert finished["status"] == "done"
    assert finished["current_node"] == "join"
    assert sent == [approval["idempotency_key"]]
    assert _completed(run_id) == ["collect", "draft", "join"]

    again = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    assert again["status"] == "done"
    assert sent == [approval["idempotency_key"]]


@pytestmark_db
def test_no_receipt_stops_and_resume_does_not_send() -> None:
    tenant_id, user_id = _ids()
    run_id = f"run_{uuid.uuid4().hex}"
    sent: list[int] = []

    def sender(_arguments: dict) -> str:
        sent.append(1)
        raise EffectError("smtp dropped the receipt", kind=EffectKind.NO_RECEIPT)

    DurableRuns().run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="发一封",
        idempotency_key=f"idem_{run_id}",
    )
    first = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    second = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    assert first["status"] == "suspended"
    assert second["status"] == "suspended"
    assert sent == [1]
    approval, _run_key = _approval(run_id)
    assert _effect_row(tenant_id, approval["idempotency_key"])["status"] == "unknown"


@pytestmark_db
def test_client_error_fails_without_a_second_send() -> None:
    tenant_id, user_id = _ids()
    run_id = f"run_{uuid.uuid4().hex}"
    sent: list[int] = []

    def sender(_arguments: dict) -> str:
        sent.append(1)
        raise EffectError("recipient rejected", kind=EffectKind.CLIENT)

    DurableRuns().run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="发一封",
        idempotency_key=f"idem_{run_id}",
    )
    failed = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    again = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    assert failed["status"] == "failed"
    assert again["status"] == "failed"
    assert sent == [1]


@pytestmark_db
def test_in_progress_left_by_a_crash_is_not_retried() -> None:
    tenant_id, user_id = _ids()
    run_id = f"run_{uuid.uuid4().hex}"
    sent: list[int] = []
    DurableRuns().run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="发一封",
        idempotency_key=f"idem_{run_id}",
    )
    approval, _run_key = _approval(run_id)
    conn = connect()
    try:
        migrate(conn)
        conn.execute(
            """
            INSERT INTO idempotency_keys (key, tenant_id, run_id, action_type, status)
            VALUES (%s, %s, %s, 'send_email', 'in_progress')
            """,
            (approval["idempotency_key"], tenant_id, run_id),
        )
        conn.commit()
    finally:
        conn.close()

    def sender(_arguments: dict) -> str:
        sent.append(1)
        return "rcpt-1"

    stopped = DurableRuns(sender=sender).resume(run_id, tenant_id=tenant_id)
    assert stopped["status"] == "suspended"
    assert sent == []
    assert _effect_row(tenant_id, approval["idempotency_key"])["status"] == "unknown"


def _approval(run_id: str) -> tuple[dict, str]:
    conn = connect()
    try:
        migrate(conn)
        approval = conn.execute(
            "SELECT status, idempotency_key FROM approvals WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        run_key = conn.execute("SELECT idempotency_key FROM runs WHERE id = %s", (run_id,)).fetchone()[
            "idempotency_key"
        ]
        conn.commit()
    finally:
        conn.close()
    return approval, run_key


def _effect_row(tenant_id: str, key: str) -> dict | None:
    conn = connect()
    try:
        migrate(conn)
        row = conn.execute(
            "SELECT status, result_ref FROM idempotency_keys WHERE tenant_id = %s AND key = %s",
            (tenant_id, key),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    return row


def _completed(run_id: str) -> list[str]:
    conn = connect()
    try:
        migrate(conn)
        row = conn.execute("SELECT state_json FROM runs WHERE id = %s", (run_id,)).fetchone()
        conn.commit()
    finally:
        conn.close()
    return list((row["state_json"] or {}).get("completed") or [])
