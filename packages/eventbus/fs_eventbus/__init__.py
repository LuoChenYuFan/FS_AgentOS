"""Event bus protocol and in-memory adapter (Day-1, no Redis required)."""

from fs_eventbus.bus import EventBus, InMemoryEventBus, Message

__all__ = ["EventBus", "InMemoryEventBus", "Message"]
