"""Gateway settings. Secrets come from the environment, not from the WebGL build."""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FS_GATEWAY_", extra="ignore")

    jwt_secret: str = "dev-only-change-me-use-a-32-byte-secret"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 7
    session_ttl_seconds: int = 60 * 60 * 8
    password_iterations: int = 20_000
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    login_max_failures: int = 5
    login_lock_seconds: int = 300
    tenant_rate_limit: int = 120
    rate_window_seconds: int = 60
    site_question_limit: int = 10
    site_question_window_seconds: int = 60 * 60
    bootstrap_username: str | None = None
    bootstrap_password: str | None = None
    bootstrap_user_id: str = "user_dev"
    bootstrap_tenant_id: str = "tenant_dev"
    bootstrap_display_name: str = ""
    bootstrap_scopes: str = "tasks:write,kb:read"
    admin_username: str | None = None
    admin_password: str | None = None
    admin_user_id: str = "user_admin"
    admin_tenant_id: str = "tenant_dev"
    admin_scopes: str = "tasks:write,kb:read,platform:admin"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def bootstrap_scope_list(self) -> list[str]:
        return [item.strip() for item in self.bootstrap_scopes.split(",") if item.strip()]

    @property
    def admin_scope_list(self) -> list[str]:
        return [item.strip() for item in self.admin_scopes.split(",") if item.strip()]

    @model_validator(mode="after")
    def browser_rules(self) -> GatewaySettings:
        if self.cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("cookie_samesite must be lax, strict, or none")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None requires Secure cookies")
        if "*" in self.cors_origin_list:
            raise ValueError("CORS allowlist cannot contain * when cookies are used")
        if self.site_question_limit < 1:
            raise ValueError("site_question_limit must be at least 1")
        if self.site_question_window_seconds < 1:
            raise ValueError("site_question_window_seconds must be at least 1")
        return self


class AuthError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
