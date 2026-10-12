"""Browser cookies for a logged-in session."""

from __future__ import annotations

from fastapi.responses import Response

from fs_gateway.context import CSRF_COOKIE, REFRESH_COOKIE, SESSION_COOKIE, LoginResult
from fs_gateway.settings import GatewaySettings


def set_auth_cookies(response: Response, settings: GatewaySettings, result: LoginResult) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }
    response.set_cookie(
        SESSION_COOKIE,
        result.session_id,
        httponly=True,
        max_age=settings.session_ttl_seconds,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        result.refresh_token,
        httponly=True,
        max_age=settings.refresh_token_ttl_seconds,
        **common,
    )
    response.set_cookie(
        CSRF_COOKIE,
        result.csrf_token,
        httponly=False,
        max_age=settings.session_ttl_seconds,
        **common,
    )


def clear_auth_cookies(response: Response, settings: GatewaySettings) -> None:
    for name in (SESSION_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )
