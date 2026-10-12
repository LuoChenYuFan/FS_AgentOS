"""Browser sessions. The cookie holds only the session id.

Replace this process-local store with Redis when more than one gateway runs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fs_common import new_id


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    user_id: str
    tenant_id: str
    scopes: list[str]
    csrf_token: str
    expires_at: float


@dataclass
class SessionStore:
    _items: dict[str, SessionRecord] = field(default_factory=dict)

    def create(
        self,
        *,
        user_id: str,
        tenant_id: str,
        scopes: list[str],
        csrf_token: str,
        ttl_seconds: int,
    ) -> SessionRecord:
        record = SessionRecord(
            session_id=new_id("sess"),
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=list(scopes),
            csrf_token=csrf_token,
            expires_at=time.time() + ttl_seconds,
        )
        self._items[record.session_id] = record
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        record = self._items.get(session_id)
        if record is None:
            return None
        if record.expires_at <= time.time():
            self._items.pop(session_id, None)
            return None
        return record

    def delete(self, session_id: str) -> None:
        self._items.pop(session_id, None)
