"""Public resume questions: ten per visitor per hour, and visitors stay apart."""

from fastapi.testclient import TestClient
from fs_gateway.auth.guards import VisitorQuestionLimiter, question_limit_message
from fs_gateway.context import GatewayState
from fs_gateway.main import create_app
from fs_gateway.platform.store import MemoryPlatformStore
from fs_gateway.settings import AuthError, GatewaySettings
from fs_orchestrator import ThinOrchestrator
from fs_rag_service import build_memory_rag


def test_one_visitor_locks_without_touching_another() -> None:
    clock = {"now": 1_000.0}
    limiter = VisitorQuestionLimiter(
        limit=10,
        window_seconds=3600,
        message=question_limit_message(3600),
        _clock=lambda: clock["now"],
    )

    for _ in range(10):
        limiter.admit("site:tenant_zhang:visit_a")
    try:
        limiter.admit("site:tenant_zhang:visit_a")
        raise AssertionError("the 11th question should be refused")
    except AuthError as exc:
        assert exc.code == "rate_limited"
        assert exc.status_code == 429
        assert str(exc) == "提问过于频繁，为控制 token 消耗，请于 1 小时后再提问。"

    limiter.admit("site:tenant_zhang:visit_b")
    assert limiter.retry_after("site:tenant_zhang:visit_a") == 3600

    clock["now"] += 3599
    try:
        limiter.admit("site:tenant_zhang:visit_a")
        raise AssertionError("the lock should still be in force")
    except AuthError:
        pass

    clock["now"] += 2
    limiter.admit("site:tenant_zhang:visit_a")


def test_a_quiet_hour_starts_a_fresh_quota() -> None:
    clock = {"now": 5_000.0}
    limiter = VisitorQuestionLimiter(
        limit=2,
        window_seconds=3600,
        message=question_limit_message(3600),
        _clock=lambda: clock["now"],
    )
    limiter.admit("visitor")
    limiter.admit("visitor")
    clock["now"] += 3600
    limiter.admit("visitor")
    limiter.admit("visitor")


def test_interviewers_on_the_same_resume_do_not_share_a_quota() -> None:
    settings = GatewaySettings(
        jwt_secret="test-secret-must-be-at-least-32-bytes",
        password_iterations=1000,
        cors_origins="http://localhost:8000",
        bootstrap_username="unity",
        bootstrap_password="unity-secret",
        bootstrap_tenant_id="tenant_yufan",
        admin_username="admin",
        admin_password="admin-secret",
        admin_scopes="tasks:write,kb:read,platform:admin",
        site_question_limit=1,
        site_question_window_seconds=3600,
    )
    state = GatewayState.create(settings, platform=MemoryPlatformStore())
    app = create_app(settings, state, ThinOrchestrator(), rag=build_memory_rag())
    alpha = TestClient(app)
    beta = TestClient(app)

    first = alpha.post("/v1/sites/yufan/tasks", json={"text": "你是谁"})
    assert first.status_code == 200
    assert first.json().get("code") != "rate_limited"
    visitor = first.json()["conversation_id"]

    blocked = alpha.post("/v1/sites/yufan/tasks", json={"text": "再问一次"})
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["code"] == "rate_limited"
    assert body["reply"] == "提问过于频繁，为控制 token 消耗，请于 1 小时后再提问。"
    assert body["conversation_id"] == visitor
    assert body["slug"] == "yufan"
    assert blocked.headers["retry-after"] == "3600"
    assert "fs_site_visitor" in blocked.headers["set-cookie"]

    other = beta.post("/v1/sites/yufan/tasks", json={"text": "你是谁"})
    assert other.status_code == 200
    assert other.json().get("code") != "rate_limited"
    assert other.json()["conversation_id"] != visitor
