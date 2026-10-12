"""One shared cap for in-flight model HTTP calls.

Coroutines wait without occupying a worker thread. Calls that are still
synchronous, such as the planner running inside the I/O pool, take the same cap.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

_GATE: ModelGate | None = None


class ModelGate:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._idle = self.limit
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._lock = threading.Lock()
        self._waiting = threading.Condition(self._lock)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()

    @contextmanager
    def blocking(self) -> Iterator[None]:
        self.acquire_blocking()
        try:
            yield
        finally:
            self.release()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._idle > 0:
                self._idle -= 1
                return
            future: asyncio.Future[None] = loop.create_future()
            self._waiters.append(future)
        try:
            await future
        except asyncio.CancelledError:
            self._drop_waiter(future)
            raise

    def acquire_blocking(self) -> None:
        with self._waiting:
            while self._idle <= 0:
                self._waiting.wait()
            self._idle -= 1

    def release(self) -> None:
        grant: asyncio.Future[None] | None = None
        with self._lock:
            while self._waiters:
                future = self._waiters.popleft()
                if not future.done():
                    grant = future
                    break
            if grant is None:
                self._idle += 1
                self._waiting.notify()
        if grant is not None:
            grant.get_loop().call_soon_threadsafe(_resolve, grant)

    def _drop_waiter(self, future: asyncio.Future[None]) -> None:
        granted = False
        with self._lock:
            try:
                self._waiters.remove(future)
            except ValueError:
                granted = future.done() and not future.cancelled()
        if granted:
            self.release()


def model_gate() -> ModelGate:
    global _GATE
    if _GATE is None:
        raw = os.environ.get("FS_MODEL_MAX_CONCURRENCY", "8")
        try:
            limit = int(raw)
        except ValueError:
            limit = 8
        _GATE = ModelGate(limit)
    return _GATE


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)
