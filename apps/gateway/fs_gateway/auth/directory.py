"""Local accounts until an enterprise IdP is connected."""

from __future__ import annotations

from dataclasses import dataclass, field

from fs_gateway.auth.passwords import hash_password, verify_password
from fs_gateway.platform.store import PlatformStore, StoredUser


@dataclass(slots=True)
class UserAccount:
    user_id: str
    tenant_id: str
    username: str
    password_hash: str
    scopes: list[str]
    is_platform_admin: bool = False


def account_from_stored(row: StoredUser) -> UserAccount:
    return UserAccount(
        user_id=row.user_id,
        tenant_id=row.tenant_id,
        username=row.username,
        password_hash=row.password_hash,
        scopes=list(row.scopes),
        is_platform_admin=row.is_platform_admin,
    )


@dataclass
class UserDirectory:
    iterations: int = 20_000
    platform: PlatformStore | None = None
    _by_username: dict[str, UserAccount] = field(default_factory=dict)

    def add(
        self,
        *,
        username: str,
        password: str,
        user_id: str,
        tenant_id: str,
        scopes: list[str],
        is_platform_admin: bool = False,
    ) -> UserAccount:
        if self.platform is not None:
            stored = self.platform.upsert_user(
                user_id=user_id,
                tenant_id=tenant_id,
                username=username,
                password=password,
                scopes=scopes,
                is_platform_admin=is_platform_admin,
                password_iterations=self.iterations,
            )
            account = account_from_stored(stored)
            self._by_username[username] = account
            return account
        account = UserAccount(
            user_id=user_id,
            tenant_id=tenant_id,
            username=username,
            password_hash=hash_password(password, iterations=self.iterations),
            scopes=list(scopes),
            is_platform_admin=is_platform_admin,
        )
        self._by_username[username] = account
        return account

    def authenticate(self, username: str, password: str) -> UserAccount | None:
        if self.platform is not None:
            stored = self.platform.authenticate(username, password)
            if stored is None:
                return None
            account = account_from_stored(stored)
            self._by_username[username] = account
            return account
        account = self._by_username.get(username)
        if account is None or not verify_password(password, account.password_hash):
            return None
        return account

    def get(self, username: str) -> UserAccount | None:
        if self.platform is not None:
            stored = self.platform.get_user(username)
            if stored is None:
                return None
            return account_from_stored(stored)
        return self._by_username.get(username)
