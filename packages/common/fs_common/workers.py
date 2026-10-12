"""Thread pool for database, search, and object-storage clients.

Those libraries block. Model HTTP stays on the event loop and does not take a
worker from this pool.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

_POOL: ThreadPoolExecutor | None = None


def io_pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        raw = os.environ.get("FS_IO_MAX_WORKERS", "32")
        try:
            workers = int(raw)
        except ValueError:
            workers = 32
        _POOL = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="fs-io")
    return _POOL


async def run_io[T](func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(io_pool(), partial(func, *args, **kwargs))
