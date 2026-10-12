"""Public site APIs (no login): published resume JSON for Unity."""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from fs_gateway.context import GatewayState
from fs_gateway.settings import GatewaySettings

# One browser (or one Unity page) keeps this cookie so follow-up turns stay
# on the same guest. Another interviewer does not share the cookie jar.
SITE_VISITOR_COOKIE = "fs_site_visitor"
_SIGNED_VISITOR = re.compile(r"^visit_([0-9a-f]{32})_([0-9a-f]{16})$")


def register_sites(app: FastAPI) -> None:
    @app.get("/v1/sites/{slug}/resume")
    async def public_resume(slug: str, request: Request) -> JSONResponse:
        gateway = _gateway(request)
        payload = gateway.platform.public_resume(slug.strip().lower())
        if payload is None:
            return JSONResponse(
                {"detail": "resume not found", "code": "not_found"},
                status_code=404,
            )
        return JSONResponse(payload)


def guest_user_id(visitor_id: str) -> str:
    """Dialogue key for one interviewer. Never the tenant's login user."""
    return f"guest_{visitor_id}"


def mint_visitor_id(secret: str) -> str:
    """Random id plus a short signature. Clients cannot invent a shared one."""
    raw = uuid.uuid4().hex
    return f"visit_{raw}_{_sign_visitor(raw, secret)}"


def valid_visitor_id(value: object, secret: str) -> str | None:
    """Accept only ids this gateway signed. Reject blank, default, and forgeries."""
    if not isinstance(value, str):
        return None
    matched = _SIGNED_VISITOR.fullmatch(value.strip())
    if matched is None:
        return None
    raw, signature = matched.group(1), matched.group(2)
    if not hmac.compare_digest(signature, _sign_visitor(raw, secret)):
        return None
    return f"visit_{raw}_{signature}"


def visitor_from_request(
    request: Request, body: dict[str, Any] | None, secret: str
) -> str | None:
    """Signed body id wins, then query, then cookie. Unsigned ids are ignored."""
    raw: object = None
    if isinstance(body, dict):
        raw = body.get("conversation_id")
    found = valid_visitor_id(raw, secret)
    if found:
        return found
    found = valid_visitor_id(request.query_params.get("conversation_id"), secret)
    if found:
        return found
    return valid_visitor_id(request.cookies.get(SITE_VISITOR_COOKIE), secret)


def _sign_visitor(raw: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:16]


def attach_visitor_cookie(
    response: Response, settings: GatewaySettings, slug: str, visitor_id: str
) -> None:
    response.set_cookie(
        SITE_VISITOR_COOKIE,
        visitor_id,
        httponly=True,
        max_age=settings.session_ttl_seconds,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        path=f"/v1/sites/{slug}",
    )


def _gateway(request: Request) -> GatewayState:
    gateway = request.app.state.gateway
    if not isinstance(gateway, GatewayState):
        return GatewayState.create()
    return gateway
