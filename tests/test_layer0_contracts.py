"""Layer 0 is shapes only: models accept a ledger, and the migration names the tables."""

from pathlib import Path

import pytest
from fs_common.contracts import (
    INTENT_UNKNOWN,
    ArtifactRecord,
    AuthMethod,
    CheckpointRecord,
    ConditionOp,
    IntentDecision,
    IntentLayer,
    IntentRoute,
    Principal,
    RunRecord,
    RunStatus,
    RuntimeState,
    TaskCondition,
    TaskDag,
    TaskNode,
)
from pydantic import ValidationError

MIGRATION = Path(__file__).resolve().parents[1] / "deploy" / "migrations" / "0001_layer0_contracts.sql"
TABLES = (
    "sessions",
    "runs",
    "tasks",
    "checkpoints",
    "artifacts",
    "approvals",
    "idempotency_keys",
    "audit_log",
    "intent_decisions",
)


def test_principal_is_the_only_identity_card() -> None:
    principal = Principal(
        user_id="user_1",
        tenant_id="tenant_1",
        scopes=["tasks:write"],
        auth_method=AuthMethod.JWT,
    )
    assert principal.auth_method is AuthMethod.JWT


def test_runtime_state_keeps_results_outside_messages() -> None:
    state = RuntimeState(
        messages=[{"role": "user", "content": "查天气再发邮件"}],
        plan=TaskDag(
            run_id="run_1",
            tasks=[
                TaskNode(id=1, agent="聊天助手", output_key="weather"),
                TaskNode(
                    id=2,
                    agent="发邮件",
                    deps=[1],
                    args={"body": "明天 ${{weather.summary}}"},
                    condition=TaskCondition(field="weather.temp", op=ConditionOp.LT, value=30),
                ),
            ],
        ),
        task_results={"weather": {"summary": "晴", "temp": 26}},
        conditions_met={"2": True},
    )
    restored = RuntimeState.model_validate(state.model_dump())
    assert restored.task_results["weather"]["temp"] == 26
    assert restored.plan is not None
    assert restored.plan.tasks[1].condition is not None
    assert restored.plan.tasks[1].condition.op is ConditionOp.LT


def test_checkpoint_stores_a_full_state_snapshot() -> None:
    checkpoint = CheckpointRecord(
        id="ckpt_1",
        run_id="run_1",
        node_name="聊天助手",
        step_seq=1,
        state_snapshot=RuntimeState(task_results={"weather": {"summary": "晴"}}),
    )
    assert checkpoint.state_snapshot.task_results["weather"]["summary"] == "晴"


def test_run_records_who_asked_and_where_it_stopped() -> None:
    run = RunRecord(
        id="run_1",
        session_id="sess_1",
        tenant_id="tenant_1",
        user_input="查天气",
        idempotency_key="idem_1",
        status=RunStatus.SUSPENDED,
        current_node="聊天助手",
    )
    assert run.current_node == "聊天助手"


def test_unknown_intent_cannot_pass() -> None:
    with pytest.raises(ValidationError):
        IntentDecision(
            intent=INTENT_UNKNOWN,
            route=IntentRoute.UNKNOWN,
            confidence=0.2,
            layer_hit=IntentLayer.NONE,
            passed=True,
        )


def test_unknown_intent_is_a_valid_stop() -> None:
    decision = IntentDecision(
        intent=INTENT_UNKNOWN,
        route=IntentRoute.UNKNOWN,
        confidence=0.0,
        layer_hit=IntentLayer.NONE,
        passed=False,
    )
    assert decision.route is IntentRoute.UNKNOWN


def test_dag_rejects_a_dependency_on_a_missing_task() -> None:
    with pytest.raises(ValidationError):
        TaskDag(run_id="run_1", tasks=[TaskNode(id=2, agent="发邮件", deps=[1])])


def test_artifact_needs_a_body_or_an_oss_pointer() -> None:
    with pytest.raises(ValidationError):
        ArtifactRecord(id="art_1", run_id="run_1", task_id="task_1", output_key="weather")

    pointer = ArtifactRecord(
        id="art_2",
        run_id="run_1",
        task_id="task_1",
        output_key="weather",
        payload_ref="oss://bucket/weather.json",
    )
    assert pointer.payload is None


def test_migration_creates_the_contract_tables() -> None:
    script = MIGRATION.read_text(encoding="utf-8")
    for name in TABLES:
        assert f"CREATE TABLE {name} " in script
    assert "state_snapshot JSONB NOT NULL" in script
    assert "UNIQUE (tenant_id, idempotency_key)" in script
