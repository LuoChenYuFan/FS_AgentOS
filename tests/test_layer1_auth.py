"""Layer 1: no credential gets in, and every door produces the same identity card."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from fs_common import NotImplementedYet
from fs_gateway.auth.external import MtlsAuthenticator, OidcAuthenticator
from fs_gateway.context import GatewayState
from fs_gateway.main import create_app
from fs_gateway.settings import GatewaySettings
from fs_orchestrator import ThinOrchestrator
from pydantic import ValidationError


class SpyOrchestrator(ThinOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    async def run(self, *, task_id: str | None = None, input_payload: dict[str, Any]) -> dict[str, str]:
        self.calls += 1
        self.payloads.append(input_payload)
        return await super().run(task_id=task_id, input_payload=input_payload)


def _settings(**overrides: object) -> GatewaySettings:
    values: dict[str, object] = {
        "jwt_secret": "test-secret-must-be-at-least-32-bytes",
        "password_iterations": 1000,
        "cors_origins": "http://localhost:8000",
    }
    values.update(overrides)
    return GatewaySettings(**values)  # type: ignore[arg-type]


def _client(scopes: list[str] | None = None, **overrides: object) -> tuple[TestClient, GatewayState, SpyOrchestrator]:
    settings = _settings(**overrides)
    state = GatewayState.create(settings)
    state.users.add(
        username="ada",
        password="secret",
        user_id="user_ada",
        tenant_id="tenant_a",
        scopes=scopes or ["tasks:write", "kb:read:hr", "apikeys:manage"],
    )
    spy = SpyOrchestrator()
    return TestClient(create_app(settings, state, spy)), state, spy


def _identity(body: dict[str, Any]) -> dict[str, Any]:
    return {"user_id": body["user_id"], "tenant_id": body["tenant_id"], "scopes": body["scopes"]}


def test_health_stays_open() -> None:
    client, _, _ = _client()
    assert client.get("/health").json()["status"] == "ok"


def test_task_without_credentials_is_rejected() -> None:
    client, _, spy = _client()
    response = client.post("/v1/tasks", json={"text": "hello"})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert spy.calls == 0


def test_jwt_api_key_and_cookie_share_one_identity() -> None:
    client, state, _ = _client()
    login = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    csrf = login.json()["csrf_token"]

    by_jwt = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    by_cookie = client.get("/v1/me")
    issued = client.post("/v1/api-keys", headers={"Authorization": f"Bearer {token}"})
    by_key = client.get("/v1/me", headers={"X-API-Key": issued.json()["api_key"]})

    assert by_jwt.json()["auth_method"] == "jwt"
    assert by_cookie.json()["auth_method"] == "cookie"
    assert by_key.json()["auth_method"] == "api_key"
    assert _identity(by_jwt.json()) == _identity(by_cookie.json()) == _identity(by_key.json())
    assert csrf
    assert state.audit[-1].success is True


def test_cookie_post_requires_csrf_and_rejects_foreign_origin() -> None:
    client, _, _ = _client()
    client.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    missing = client.post("/v1/tasks", json={"text": "hello"})
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"

    foreign = client.post(
        "/v1/tasks",
        json={"text": "hello"},
        headers={"X-CSRF-Token": client.cookies.get("fs_csrf"), "Origin": "https://evil.example"},
    )
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "forbidden"


def test_same_idempotency_key_does_not_open_a_second_run() -> None:
    client, _, spy = _client()
    login = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "Idempotency-Key": "idem_1",
    }
    first = client.post("/v1/tasks", json={"text": "帮我发一封邮件"}, headers=headers)
    second = client.post("/v1/tasks", json={"text": "帮我发一封邮件"}, headers=headers)
    conflict = client.post("/v1/tasks", json={"text": "帮我查天气"}, headers=headers)
    assert first.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"]
    assert spy.calls == 1
    assert conflict.status_code == 409


def test_client_cannot_choose_the_principal() -> None:
    client, _, spy = _client()
    login = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    client.post(
        "/v1/tasks",
        json={"text": "帮我发一封邮件", "user_id": "hacker", "tenant_id": "other", "principal": {"user_id": "hacker"}},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert spy.payloads[0]["user_id"] == "user_ada"
    assert spy.payloads[0]["tenant_id"] == "tenant_a"
    assert spy.payloads[0]["principal"]["user_id"] == "user_ada"


def test_missing_scope_and_knowledge_scope_are_forbidden() -> None:
    client, _, spy = _client(scopes=["kb:read:hr"])
    login = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    denied = client.post("/v1/tasks", json={"text": "hello"}, headers=headers)
    assert denied.status_code == 403
    assert spy.calls == 0

    allowed, _, _ = _client(scopes=["tasks:write", "kb:read:hr"])
    login = allowed.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    wrong_kb = allowed.post("/v1/tasks", json={"knowledge_scope": "finance"}, headers=headers)
    right_kb = allowed.post("/v1/tasks", json={"knowledge_scope": "hr"}, headers=headers)
    assert wrong_kb.status_code == 403
    assert right_kb.status_code == 200


def test_refresh_rotates_and_logout_revokes_access() -> None:
    client, _, _ = _client()
    login = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"}).json()
    refreshed = client.post("/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 200
    reused = client.post("/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert reused.status_code == 401

    bearer = {"Authorization": f"Bearer {login['access_token']}"}
    assert client.get("/v1/me", headers=bearer).status_code == 200
    assert client.post("/v1/auth/logout", headers=bearer).status_code == 200
    assert client.get("/v1/me", headers=bearer).status_code == 401


def test_login_locks_after_repeated_failures() -> None:
    client, _, _ = _client(login_max_failures=2, login_lock_seconds=300)
    assert client.post("/v1/auth/login", json={"username": "ada", "password": "no"}).status_code == 401
    assert client.post("/v1/auth/login", json={"username": "ada", "password": "no"}).status_code == 401
    locked = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"})
    assert locked.status_code == 429
    assert locked.json()["code"] == "login_locked"


def test_revoked_api_key_stops_working() -> None:
    client, _, _ = _client()
    login = client.post("/v1/auth/login", json={"username": "ada", "password": "secret"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    issued = client.post("/v1/api-keys", headers=headers).json()
    assert client.get("/v1/me", headers={"X-API-Key": issued["api_key"]}).status_code == 200
    revoked = client.post(f"/v1/api-keys/{issued['key_id']}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert client.get("/v1/me", headers={"X-API-Key": issued["api_key"]}).status_code == 401


def test_cors_wildcard_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GatewaySettings(cors_origins="*")


def test_enterprise_login_and_mtls_are_not_wired() -> None:
    with pytest.raises(NotImplementedYet):
        OidcAuthenticator().authenticate("code")
    with pytest.raises(NotImplementedYet):
        MtlsAuthenticator().authenticate("CN=gateway")
