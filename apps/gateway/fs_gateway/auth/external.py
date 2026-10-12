"""Enterprise login and service identity. Not connected in this layer."""

from __future__ import annotations

from fs_common import NotImplementedYet, Principal


class OidcAuthenticator:
    """Authorization-code login against a company IdP. Wire this when an IdP exists."""

    def authenticate(self, authorization_code: str) -> Principal:
        _ = authorization_code
        raise NotImplementedYet("oidc.authorization_code")


class MtlsAuthenticator:
    """Service-to-service certificates. Wire this when gateways call each other."""

    def authenticate(self, peer_cert_subject: str) -> Principal:
        _ = peer_cert_subject
        raise NotImplementedYet("mtls.peer_certificate")
