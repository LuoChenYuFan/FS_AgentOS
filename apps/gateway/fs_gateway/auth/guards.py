"""Authorization, CSRF, login lock, and rate limits."""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from fs_common import Principal

from fs_gateway.settings import AuthError


def require_scope(principal: Principal, scope: str) -> None:
    if scope in principal.scopes or "*" in principal.scopes:
        return
    raise AuthError("missing permission", code="forbidden", status_code=403)


def require_knowledge_scope(principal: Principal, knowledge_scope: str) -> None:
    allowed = "kb:read" in principal.scopes or f"kb:read:{knowledge_scope}" in principal.scopes
    if allowed or "*" in principal.scopes:
        return
    raise AuthError("knowledge scope denied", code="forbidden", status_code=403)


def csrf_matches(expected: str, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(expected, presented)


def origin_allowed(
    origin: str | None,
    referer: str | None,
    allowlist: list[str],
    *,
    own_origin: str | None = None,
) -> bool:
    allowed = list(allowlist)
    if own_origin and own_origin not in allowed:
        allowed.append(own_origin)
    if origin:
        return origin in allowed
    if referer:
        parsed = urlparse(referer)
        return f"{parsed.scheme}://{parsed.netloc}" in allowed
    return True


@dataclass
class LoginGuard:
    max_failures: int
    lock_seconds: int
    _failures: dict[str, int] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def check(self, username: str) -> None:
        locked_until = self._locked_until.get(username, 0)
        if locked_until > time.time():
            raise AuthError("login temporarily locked", code="login_locked", status_code=429)

    def record_failure(self, username: str) -> None:
        count = self._failures.get(username, 0) + 1
        self._failures[username] = count
        if count >= self.max_failures:
            self._locked_until[username] = time.time() + self.lock_seconds
            self._failures[username] = 0

    def record_success(self, username: str) -> None:
        self._failures.pop(username, None)
        self._locked_until.pop(username, None)


@dataclass
class TenantRateLimiter:
    limit: int
    window_seconds: int
    _hits: dict[str, list[float]] = field(default_factory=dict)

    def check(self, tenant_id: str) -> None:
        now = time.time()
        window_start = now - self.window_seconds
        recent = [stamp for stamp in self._hits.get(tenant_id, []) if stamp >= window_start]
        if len(recent) >= self.limit:
            self._hits[tenant_id] = recent
            raise AuthError("rate limit exceeded", code="rate_limited", status_code=429)
        recent.append(now)
        self._hits[tenant_id] = recent


def question_limit_message(window_seconds: int) -> str:
    """The sentence an interviewer sees after using up the hourly quota."""
    return f"提问过于频繁，为控制 token 消耗，请于 {_wait_phrase(window_seconds)}后再提问。"


def _wait_phrase(window_seconds: int) -> str:
    if window_seconds % 3600 == 0 and window_seconds >= 3600:
        hours = window_seconds // 3600
        return "1 小时" if hours == 1 else f"{hours} 小时"
    if window_seconds % 60 == 0 and window_seconds >= 60:
        minutes = window_seconds // 60
        return "1 分钟" if minutes == 1 else f"{minutes} 分钟"
    return f"{window_seconds} 秒"


@dataclass
class VisitorQuestionLimiter:
    """How many questions one interviewer may ask before a cooldown.

    The key is one visitor on one resume. Another browser, or the same
    browser on another person's site, has its own count. After the quota
    is used inside the window, further questions wait one full window.
    """

    limit: int
    window_seconds: int
    message: str
    _clock: Callable[[], float] = field(default=time.time, repr=False)
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _used: dict[str, int] = field(default_factory=dict)
    _window_started: dict[str, float] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def admit(self, key: str) -> None:
        with self._guard:
            now = self._clock()
            locked_until = self._locked_until.get(key, 0.0)
            if locked_until > now:
                raise AuthError(self.message, code="rate_limited", status_code=429)
            if locked_until or self._window_expired(key, now):
                self._reset(key)
            used = self._used.get(key, 0)
            if used >= self.limit:
                self._locked_until[key] = now + self.window_seconds
                raise AuthError(self.message, code="rate_limited", status_code=429)
            if key not in self._window_started:
                self._window_started[key] = now
            self._used[key] = used + 1

    def retry_after(self, key: str) -> int:
        with self._guard:
            remaining = self._locked_until.get(key, 0.0) - self._clock()
            if remaining > 0:
                return max(1, int(remaining + 0.999))
            return self.window_seconds

    def _window_expired(self, key: str, now: float) -> bool:
        started = self._window_started.get(key)
        if started is None:
            return False
        return now - started >= self.window_seconds

    def _reset(self, key: str) -> None:
        self._used.pop(key, None)
        self._window_started.pop(key, None)
        self._locked_until.pop(key, None)
