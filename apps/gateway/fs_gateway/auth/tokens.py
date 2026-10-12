"""Short-lived access tokens and rotating refresh tokens.

Verification is local HS256 for this process. OIDC JWKS replaces the signer later
without changing the Principal handed to the rest of the system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fs_common import new_id

from fs_gateway.settings import AuthError, GatewaySettings


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: str
    tenant_id: str
    scopes: list[str]
    jti: str
    token_type: str


class TokenService:
    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self.revoked: set[str] = set()
        self.refresh_owner: dict[str, str] = {}
        self.refresh_by_user: dict[str, set[str]] = {}

    def issue_pair(self, *, user_id: str, tenant_id: str, scopes: list[str]) -> tuple[str, str, int]:
        access = self._issue(
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes,
            token_type="access",
            ttl_seconds=self._settings.access_token_ttl_seconds,
        )
        refresh = self._issue(
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes,
            token_type="refresh",
            ttl_seconds=self._settings.refresh_token_ttl_seconds,
        )
        refresh_claims = self.decode(refresh, expected_type="refresh")
        self._remember_refresh(refresh_claims.jti, user_id)
        return access, refresh, self._settings.access_token_ttl_seconds

    def rotate(self, refresh_token: str) -> tuple[str, str, int, TokenClaims]:
        claims = self.decode(refresh_token, expected_type="refresh")
        if claims.jti in self.revoked:
            self.revoke_user_refresh(claims.user_id)
            raise AuthError("refresh token reuse", code="refresh_reuse", status_code=401)
        if claims.jti not in self.refresh_owner:
            raise AuthError("refresh token is not active", code="invalid_token", status_code=401)
        self.revoked.add(claims.jti)
        self.refresh_owner.pop(claims.jti, None)
        self.refresh_by_user.get(claims.user_id, set()).discard(claims.jti)
        access, refresh, expires_in = self.issue_pair(
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
            scopes=claims.scopes,
        )
        return access, refresh, expires_in, claims

    def decode(self, token: str, *, expected_type: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=["HS256"],
                options={"require": ["exp", "sub", "jti", "typ"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token", code="invalid_token", status_code=401) from exc
        if payload.get("typ") != expected_type:
            raise AuthError("wrong token type", code="invalid_token", status_code=401)
        jti = str(payload["jti"])
        if jti in self.revoked and expected_type == "access":
            raise AuthError("token revoked", code="token_revoked", status_code=401)
        scopes = payload.get("scopes") or []
        if not isinstance(scopes, list):
            raise AuthError("invalid token", code="invalid_token", status_code=401)
        return TokenClaims(
            user_id=str(payload["sub"]),
            tenant_id=str(payload.get("tid") or ""),
            scopes=[str(item) for item in scopes],
            jti=jti,
            token_type=expected_type,
        )

    def revoke(self, jti: str) -> None:
        self.revoked.add(jti)

    def revoke_user_refresh(self, user_id: str) -> None:
        for jti in self.refresh_by_user.get(user_id, set()):
            self.revoked.add(jti)
            self.refresh_owner.pop(jti, None)
        self.refresh_by_user[user_id] = set()

    def _remember_refresh(self, jti: str, user_id: str) -> None:
        self.refresh_owner[jti] = user_id
        self.refresh_by_user.setdefault(user_id, set()).add(jti)

    def _issue(
        self,
        *,
        user_id: str,
        tenant_id: str,
        scopes: list[str],
        token_type: str,
        ttl_seconds: int,
    ) -> str:
        now = int(time.time())
        payload = {
            "sub": user_id,
            "tid": tenant_id,
            "scopes": scopes,
            "jti": new_id("jti"),
            "typ": token_type,
            "iat": now,
            "exp": now + ttl_seconds,
        }
        return jwt.encode(payload, self._settings.jwt_secret, algorithm="HS256")
