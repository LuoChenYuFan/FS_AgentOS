"""Authentication channels. All of them end as a Principal."""

from fs_gateway.auth.external import MtlsAuthenticator, OidcAuthenticator

__all__ = ["MtlsAuthenticator", "OidcAuthenticator"]
