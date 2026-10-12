"""Caller identity after the gateway has authenticated the request."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AuthMethod(StrEnum):
    """How the caller proved who they are. Authorization is a separate check."""

    JWT = "jwt"
    API_KEY = "api_key"
    COOKIE = "cookie"
    OIDC = "oidc"
    MTLS = "mtls"
    INTERNAL_JWT = "internal_jwt"


class Principal(BaseModel):
    """Identity card passed inward. Downstream code does not parse tokens."""

    user_id: str
    tenant_id: str
    scopes: list[str] = Field(default_factory=list)
    auth_method: AuthMethod
