"""API keys for scripts. Only the hash is kept."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from fs_common import new_id


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


@dataclass(slots=True)
class ApiKeyRecord:
    key_id: str
    key_hash: str
    user_id: str
    tenant_id: str
    scopes: list[str]
    revoked: bool = False


@dataclass
class ApiKeyStore:
    _by_hash: dict[str, ApiKeyRecord] = field(default_factory=dict)
    _by_id: dict[str, ApiKeyRecord] = field(default_factory=dict)

    def issue(self, *, user_id: str, tenant_id: str, scopes: list[str]) -> tuple[str, ApiKeyRecord]:
        raw_key = f"fsak_{new_id('key')}"
        record = ApiKeyRecord(
            key_id=new_id("apikey"),
            key_hash=_hash_key(raw_key),
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=list(scopes),
            revoked=False,
        )
        self._by_hash[record.key_hash] = record
        self._by_id[record.key_id] = record
        return raw_key, record

    def authenticate(self, raw_key: str) -> ApiKeyRecord | None:
        record = self._by_hash.get(_hash_key(raw_key))
        if record is None or record.revoked:
            return None
        return record

    def revoke(self, key_id: str, *, tenant_id: str) -> bool:
        record = self._by_id.get(key_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        record.revoked = True
        return True
