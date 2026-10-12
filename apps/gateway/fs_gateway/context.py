"""In-process door state: accounts, sessions, tokens, and the idempotency desk."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fs_common import AuthMethod, Principal, new_id

from fs_gateway.auth.api_keys import ApiKeyStore
from fs_gateway.auth.directory import UserDirectory
from fs_gateway.auth.guards import (
    LoginGuard,
    TenantRateLimiter,
    VisitorQuestionLimiter,
    csrf_matches,
    origin_allowed,
    question_limit_message,
    require_knowledge_scope,
    require_scope,
)
from fs_gateway.auth.sessions import SessionStore
from fs_gateway.auth.tokens import TokenService
from fs_gateway.platform.store import (
    MemoryPlatformStore,
    PlatformStore,
    PostgresPlatformStore,
    ensure_platform_bootstrap,
)
from fs_gateway.settings import AuthError, GatewaySettings

SESSION_COOKIE = "fs_session"
CSRF_COOKIE = "fs_csrf"
REFRESH_COOKIE = "fs_refresh"
CSRF_HEADER = "X-CSRF-Token"


@dataclass
class AuthAudit:
    username: str | None
    user_id: str | None
    tenant_id: str | None
    auth_method: str
    action: str
    success: bool


@dataclass
class LoginResult:
    access_token: str
    refresh_token: str
    expires_in: int
    csrf_token: str
    session_id: str
    principal: Principal


@dataclass
class StoredResponse:
    user_id: str
    fingerprint: str
    body: dict[str, Any]


@dataclass
class GatewayState:
    settings: GatewaySettings
    users: UserDirectory
    tokens: TokenService
    platform: PlatformStore = field(default_factory=MemoryPlatformStore)
    api_keys: ApiKeyStore = field(default_factory=ApiKeyStore)
    sessions: SessionStore = field(default_factory=SessionStore)
    login_guard: LoginGuard = field(init=False)
    rate_limiter: TenantRateLimiter = field(init=False)
    site_question_limiter: VisitorQuestionLimiter = field(init=False)
    audit: list[AuthAudit] = field(default_factory=list)
    idempotency: dict[tuple[str, str], StoredResponse] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.login_guard = LoginGuard(
            max_failures=self.settings.login_max_failures,
            lock_seconds=self.settings.login_lock_seconds,
        )
        self.rate_limiter = TenantRateLimiter(
            limit=self.settings.tenant_rate_limit,
            window_seconds=self.settings.rate_window_seconds,
        )
        self.site_question_limiter = VisitorQuestionLimiter(
            limit=self.settings.site_question_limit,
            window_seconds=self.settings.site_question_window_seconds,
            message=question_limit_message(self.settings.site_question_window_seconds),
        )
        self.users.platform = self.platform

    @classmethod
    def create(
        cls,
        settings: GatewaySettings | None = None,
        *,
        platform: PlatformStore | None = None,
    ) -> GatewayState:
        resolved = settings or GatewaySettings()
        # Tests call create() without a store and must stay in-memory.
        # Production wiring passes Postgres via create_app(...).
        store = platform if platform is not None else MemoryPlatformStore()
        state = cls(
            settings=resolved,
            users=UserDirectory(iterations=resolved.password_iterations, platform=store),
            tokens=TokenService(resolved),
            platform=store,
        )
        ensure_platform_bootstrap(store, settings=resolved)
        return state

    def login(self, username: str, password: str) -> LoginResult:
        self.login_guard.check(username)
        account = self.users.authenticate(username, password)
        if account is None:
            self.login_guard.record_failure(username)
            self._audit(username, None, None, "password", "login", False)
            raise AuthError("invalid credentials", code="invalid_credentials", status_code=401)
        self.login_guard.record_success(username)
        result = self._open_session(account.user_id, account.tenant_id, account.scopes, AuthMethod.JWT)
        self._audit(username, account.user_id, account.tenant_id, "password", "login", True)
        return result

    def refresh(self, refresh_token: str) -> LoginResult:
        access, refresh, expires_in, claims = self.tokens.rotate(refresh_token)
        session = self.sessions.create(
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
            scopes=claims.scopes,
            csrf_token=secrets.token_urlsafe(32),
            ttl_seconds=self.settings.session_ttl_seconds,
        )
        principal = Principal(
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
            scopes=list(claims.scopes),
            auth_method=AuthMethod.JWT,
        )
        self._audit(None, claims.user_id, claims.tenant_id, "jwt", "refresh", True)
        return LoginResult(
            access_token=access,
            refresh_token=refresh,
            expires_in=expires_in,
            csrf_token=session.csrf_token,
            session_id=session.session_id,
            principal=principal,
        )

    def logout(self, *, session_id: str | None, access_jti: str | None, refresh_token: str | None) -> None:
        if session_id:
            self.sessions.delete(session_id)
        if access_jti:
            self.tokens.revoke(access_jti)
        if refresh_token:
            try:
                claims = self.tokens.decode(refresh_token, expected_type="refresh")
            except AuthError:
                return
            self.tokens.revoke(claims.jti)
            self.tokens.refresh_owner.pop(claims.jti, None)

    def issue_api_key(self, principal: Principal, scopes: list[str] | None) -> tuple[str, str]:
        require_scope(principal, "apikeys:manage")
        granted = scopes or list(principal.scopes)
        unknown = [scope for scope in granted if scope not in principal.scopes and "*" not in principal.scopes]
        if unknown:
            raise AuthError("cannot grant a scope you do not have", code="forbidden", status_code=403)
        raw_key, record = self.api_keys.issue(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            scopes=granted,
        )
        return raw_key, record.key_id

    def authenticate(self, request: Request) -> Principal:
        authorization = request.headers.get("authorization")
        if authorization:
            return self._principal_from_bearer(authorization)
        api_key = request.headers.get("x-api-key")
        if api_key:
            return self._principal_from_api_key(api_key)
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            return self._principal_from_cookie(request, session_id)
        raise AuthError("credentials required", code="unauthorized", status_code=401)

    def begin_task(self, principal: Principal, body: dict[str, Any], idempotency_key: str) -> dict[str, Any] | None:
        require_scope(principal, "tasks:write")
        knowledge_scope = body.get("knowledge_scope")
        if isinstance(knowledge_scope, str) and knowledge_scope:
            require_knowledge_scope(principal, knowledge_scope)
        fingerprint = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        slot = (principal.tenant_id, idempotency_key)
        existing = self.idempotency.get(slot)
        if existing is None:
            self.rate_limiter.check(principal.tenant_id)
            return None
        if existing.user_id != principal.user_id or existing.fingerprint != fingerprint:
            raise AuthError("idempotency key reused", code="idempotency_conflict", status_code=409)
        return dict(existing.body)

    def finish_task(
        self,
        principal: Principal,
        body: dict[str, Any],
        idempotency_key: str,
        response: dict[str, Any],
    ) -> None:
        fingerprint = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        self.idempotency[(principal.tenant_id, idempotency_key)] = StoredResponse(
            user_id=principal.user_id,
            fingerprint=fingerprint,
            body=dict(response),
        )

    def _open_session(self, user_id: str, tenant_id: str, scopes: list[str], method: AuthMethod) -> LoginResult:
        access, refresh, expires_in = self.tokens.issue_pair(
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes,
        )
        csrf_token = secrets.token_urlsafe(32)
        session = self.sessions.create(
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes,
            csrf_token=csrf_token,
            ttl_seconds=self.settings.session_ttl_seconds,
        )
        return LoginResult(
            access_token=access,
            refresh_token=refresh,
            expires_in=expires_in,
            csrf_token=csrf_token,
            session_id=session.session_id,
            principal=Principal(
                user_id=user_id,
                tenant_id=tenant_id,
                scopes=list(scopes),
                auth_method=method,
            ),
        )

    def _principal_from_bearer(self, authorization: str) -> Principal:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthError("invalid token", code="invalid_token", status_code=401)
        claims = self.tokens.decode(token, expected_type="access")
        self._audit(None, claims.user_id, claims.tenant_id, "jwt", "authenticate", True)
        return Principal(
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
            scopes=list(claims.scopes),
            auth_method=AuthMethod.JWT,
        )

    def _principal_from_api_key(self, raw_key: str) -> Principal:
        record = self.api_keys.authenticate(raw_key)
        if record is None:
            self._audit(None, None, None, "api_key", "authenticate", False)
            raise AuthError("invalid credentials", code="invalid_credentials", status_code=401)
        self._audit(None, record.user_id, record.tenant_id, "api_key", "authenticate", True)
        return Principal(
            user_id=record.user_id,
            tenant_id=record.tenant_id,
            scopes=list(record.scopes),
            auth_method=AuthMethod.API_KEY,
        )

    def _principal_from_cookie(self, request: Request, session_id: str) -> Principal:
        session = self.sessions.get(session_id)
        if session is None:
            raise AuthError("invalid session", code="invalid_session", status_code=401)
        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            if not origin_allowed(
                request.headers.get("origin"),
                request.headers.get("referer"),
                self.settings.cors_origin_list,
                own_origin=f"{request.url.scheme}://{request.url.netloc}",
            ):
                raise AuthError("origin not allowed", code="forbidden", status_code=403)
            if not csrf_matches(session.csrf_token, request.headers.get(CSRF_HEADER)):
                raise AuthError("csrf check failed", code="csrf_failed", status_code=403)
        self._audit(None, session.user_id, session.tenant_id, "cookie", "authenticate", True)
        return Principal(
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            scopes=list(session.scopes),
            auth_method=AuthMethod.COOKIE,
        )

    def _audit(
        self,
        username: str | None,
        user_id: str | None,
        tenant_id: str | None,
        auth_method: str,
        action: str,
        success: bool,
    ) -> None:
        self.audit.append(
            AuthAudit(
                username=username,
                user_id=user_id,
                tenant_id=tenant_id,
                auth_method=auth_method,
                action=action,
                success=success,
            )
        )


def default_platform_store() -> PlatformStore:
    """Postgres when configured; otherwise memory. Used by create_app only."""
    try:
        from fs_orchestrator.postgres import connect, migrate, postgres_configured

        if postgres_configured():
            with connect() as conn:
                migrate(conn)
            return PostgresPlatformStore(connect)
    except Exception:
        pass
    return MemoryPlatformStore()


def is_platform_admin(principal: Principal) -> bool:
    return "platform:admin" in principal.scopes or "*" in principal.scopes


def new_trace_id(request: Request) -> str:
    incoming = request.headers.get("x-trace-id")
    return incoming or new_id("trace")
