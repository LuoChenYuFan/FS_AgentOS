"""Layer 2: a killed process resumes the same run from the last committed node."""

import uuid

import pytest
from fs_orchestrator.ledger import DurableRuns
from fs_orchestrator.postgres import connect, migrate, postgres_configured

pytestmark = pytest.mark.skipif(not postgres_configured(), reason="PostgreSQL credentials are not configured")


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"tenant_{suffix}", f"user_{suffix}"


def test_crash_after_one_node_resumes_on_a_new_worker() -> None:
    tenant_id, user_id = _ids()
    run_id = f"run_{uuid.uuid4().hex}"
    first = DurableRuns()
    started = first.run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="帮我写一封邮件",
        idempotency_key=f"idem_{run_id}",
        max_steps=1,
    )
    assert started["status"] == "running"
    assert started["current_node"] == "collect"

    second = DurableRuns()
    continued = second.resume(run_id, tenant_id=tenant_id)
    assert continued["status"] == "awaiting_approval"
    assert continued["current_node"] == "draft"

    finished = DurableRuns().resume(run_id, tenant_id=tenant_id)
    assert finished["status"] == "done"
    assert finished["current_node"] == "join"

    conn = connect()
    try:
        migrate(conn)
        count = conn.execute("SELECT COUNT(*) AS n FROM checkpoints WHERE run_id = %s", (run_id,)).fetchone()["n"]
        conn.commit()
    finally:
        conn.close()
    assert count >= 3


def test_pause_and_cancel_write_run_status() -> None:
    tenant_id, user_id = _ids()
    run_id = f"run_{uuid.uuid4().hex}"
    runs = DurableRuns()
    runs.run(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input="暂停这次",
        idempotency_key=f"idem_{run_id}",
        max_steps=1,
    )
    paused = runs.pause(run_id, tenant_id=tenant_id)
    assert paused["status"] == "suspended"
    resumed = runs.resume(run_id, tenant_id=tenant_id)
    assert resumed["status"] == "awaiting_approval"
    cancelled = runs.cancel(run_id, tenant_id=tenant_id)
    assert cancelled["status"] == "failed"
    again = runs.resume(run_id, tenant_id=tenant_id)
    assert again["status"] == "failed"
