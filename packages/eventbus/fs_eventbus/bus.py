"""EventBus interface + InMemory implementation.

RedisStreamsBus / KafkaBus land behind the same interface later.
Business code must depend on EventBus only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from collections import defaultdict, deque


@dataclass(frozen=True, slots=True)
class Message:
    topic: str
    payload: dict[str, Any]
    message_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus(Protocol):
    async def publish(self, topic: str, payload: dict[str, Any]) -> str: ...

    async def subscribe(self, topic: str, *, max_messages: int = 1) -> list[Message]: ...

    async def ack(self, topic: str, message_id: str) -> None: ...

    async def nack(self, topic: str, message_id: str) -> None: ...


class InMemoryEventBus:
    """Process-local bus for local scaffolding without Redis."""

    def __init__(self) -> None:
        self._queues: dict[str, deque[Message]] = defaultdict(deque)
        self._pending: dict[str, dict[str, Message]] = defaultdict(dict)

    async def publish(self, topic: str, payload: dict[str, Any]) -> str:
        from fs_common import new_id

        message = Message(topic=topic, payload=payload, message_id=new_id("msg"))
        self._queues[topic].append(message)
        return message.message_id

    async def subscribe(self, topic: str, *, max_messages: int = 1) -> list[Message]:
        out: list[Message] = []
        queue = self._queues[topic]
        while queue and len(out) < max_messages:
            msg = queue.popleft()
            self._pending[topic][msg.message_id] = msg
            out.append(msg)
        return out

    async def ack(self, topic: str, message_id: str) -> None:
        self._pending[topic].pop(message_id, None)

    async def nack(self, topic: str, message_id: str) -> None:
        msg = self._pending[topic].pop(message_id, None)
        if msg is not None:
            self._queues[topic].appendleft(msg)
