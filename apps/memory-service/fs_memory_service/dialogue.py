"""Session blackboard: an append-only transcript plus one slot per task.

The transcript and the working slots live in the memory store (PostgreSQL, with
Redis in front when it is configured). A side question does not clear another
task's slots. Idle sessions expire so a restart, or another gateway process,
still sees the same board until that idle window ends.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from fs_memory_sdk import MemoryRecord, MemoryScope
from fs_memory_service.service import MemoryService

TRANSCRIPT_KEY = "transcript"
SLOTS_KEY = "slots"
DEFAULT_CONVERSATION = "default"
MAX_TURNS = 24
MAX_TURN_CHARS = 1500
IDLE_SECONDS = 6 * 3600
SLOT_NAMES = ("itinerary", "weather", "email", "knowledge")

_EMPTY_SLOTS = {
    "itinerary": {"origin": "", "destination": "", "mode": ""},
    "weather": {"city": "", "date": ""},
    "email": {"recipient": "", "subject": ""},
    "knowledge": {"subject": ""},
}


class DialogueState:
    def __init__(self, *, turns: list[dict[str, str]], slots: dict[str, dict[str, str]]) -> None:
        self.turns = turns
        self.slots = slots

    @classmethod
    def empty(cls) -> DialogueState:
        return cls(turns=[], slots=empty_slots())


class DialogueBoard:
    """One user's current conversation. Long-term facts stay on MemoryScope.LONG_TERM."""

    def __init__(
        self,
        memory: MemoryService,
        *,
        idle_seconds: int = IDLE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._memory = memory
        self._idle = timedelta(seconds=idle_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def in_memory(cls, **kwargs: Any) -> DialogueBoard:
        return cls(MemoryService(_DictStore(), cache=_NullCache()), **kwargs)

    def load(self, tenant_id: str, user_id: str, *, conversation_id: str = DEFAULT_CONVERSATION) -> DialogueState:
        if not tenant_id or not user_id:
            return DialogueState.empty()
        now = self._clock()
        conversation = _conversation(conversation_id)
        transcript = self._read(tenant_id, user_id, MemoryScope.SESSION, _key(TRANSCRIPT_KEY, conversation))
        working = self._read(tenant_id, user_id, MemoryScope.WORKING, _key(SLOTS_KEY, conversation))
        if _expired(transcript, working, now=now, idle=self._idle):
            self.clear(tenant_id, user_id, conversation_id=conversation)
            return DialogueState.empty()
        slots = _slots(working)
        return DialogueState(turns=_turns(transcript), slots=_with_prefs(slots, self._prefs(tenant_id, user_id)))

    def commit(
        self,
        tenant_id: str,
        user_id: str,
        *,
        user_text: str,
        assistant_text: str,
        slot_patch: dict[str, dict[str, str]] | None = None,
        conversation_id: str = DEFAULT_CONVERSATION,
    ) -> DialogueState:
        if not tenant_id or not user_id:
            return DialogueState.empty()
        conversation = _conversation(conversation_id)
        state = self.load(tenant_id, user_id, conversation_id=conversation)
        now = self._clock()
        stamp = now.isoformat()
        turns = list(state.turns)
        for role, text in (("user", user_text), ("assistant", assistant_text)):
            cleaned = text.strip()
            if not cleaned:
                continue
            turns.append({"role": role, "text": cleaned[:MAX_TURN_CHARS], "at": stamp})
        turns = turns[-MAX_TURNS:]
        slots = merge_slots(state.slots, slot_patch or {})
        self._write(
            tenant_id,
            user_id,
            MemoryScope.SESSION,
            _key(TRANSCRIPT_KEY, conversation),
            {"turns": turns, "touched_at": stamp, "conversation_id": conversation},
            _tokens(turns),
        )
        self._write(
            tenant_id,
            user_id,
            MemoryScope.WORKING,
            _key(SLOTS_KEY, conversation),
            {"slots": slots, "touched_at": stamp, "conversation_id": conversation},
            _tokens([slots]),
        )
        return DialogueState(turns=turns, slots=slots)

    def prefer(self, tenant_id: str, user_id: str, key: str, value: str) -> None:
        cleaned = value.strip()
        if not tenant_id or not user_id or not key.strip() or not cleaned:
            return
        self._write(
            tenant_id,
            user_id,
            MemoryScope.LONG_TERM,
            key.strip(),
            {"value": cleaned},
            max(1, len(cleaned) // 2),
        )

    def clear(self, tenant_id: str, user_id: str, *, conversation_id: str = DEFAULT_CONVERSATION) -> None:
        conversation = _conversation(conversation_id)
        self._memory.forget(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=MemoryScope.SESSION,
            key=_key(TRANSCRIPT_KEY, conversation),
        )
        self._memory.forget(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=MemoryScope.WORKING,
            key=_key(SLOTS_KEY, conversation),
        )

    def _prefs(self, tenant_id: str, user_id: str) -> dict[str, str]:
        facts: dict[str, str] = {}
        for item in self._memory.recall_long_term(tenant_id=tenant_id, user_id=user_id):
            key = str(item.get("key") or "")
            content = item.get("content")
            if not isinstance(content, dict):
                continue
            value = str(content.get("value") or content.get("city") or content.get("mode") or "").strip()
            if key and value:
                facts[key] = value
        return facts

    def _read(self, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> dict[str, Any]:
        found = self._memory.recall(scope, key, tenant_id=tenant_id, user_id=user_id)
        if found is None or not isinstance(found.content, dict):
            return {}
        return found.content

    def _write(
        self,
        tenant_id: str,
        user_id: str,
        scope: MemoryScope,
        key: str,
        content: dict[str, Any],
        tokens: int,
    ) -> None:
        self._memory.remember(
            MemoryRecord(
                scope=scope,
                key=key,
                content=content,
                token_estimate=tokens,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )


def empty_slots() -> dict[str, dict[str, str]]:
    return {name: dict(fields) for name, fields in _EMPTY_SLOTS.items()}


def merge_slots(
    current: dict[str, dict[str, str]],
    patch: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Fill only the fields this turn actually produced. Other tasks stay as they were."""

    merged = empty_slots()
    for name in SLOT_NAMES:
        existing = current.get(name) if isinstance(current.get(name), dict) else {}
        for field, value in existing.items():
            if field in merged[name] and isinstance(value, str) and value.strip():
                merged[name][field] = value.strip()
        incoming = patch.get(name) if isinstance(patch.get(name), dict) else {}
        for field, value in incoming.items():
            if field in merged[name] and isinstance(value, str) and value.strip():
                merged[name][field] = value.strip()
    return merged


def _turns(content: dict[str, Any]) -> list[dict[str, str]]:
    raw = content.get("turns")
    if not isinstance(raw, list):
        return []
    turns: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        text = str(item.get("text") or "").strip()
        if role not in {"user", "assistant"} or not text:
            continue
        turns.append({"role": role, "text": text, "at": str(item.get("at") or "")})
    return turns[-MAX_TURNS:]


def _slots(content: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw = content.get("slots")
    if not isinstance(raw, dict):
        return empty_slots()
    return merge_slots(empty_slots(), raw)


def _expired(
    transcript: dict[str, Any],
    working: dict[str, Any],
    *,
    now: datetime,
    idle: timedelta,
) -> bool:
    stamps = [_stamp(transcript.get("touched_at")), _stamp(working.get("touched_at"))]
    known = [item for item in stamps if item is not None]
    if not known:
        return False
    return now - max(known) > idle


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _tokens(parts: list[object]) -> int:
    return max(1, sum(len(str(part)) for part in parts) // 2)


def _conversation(value: str) -> str:
    cleaned = value.strip() or DEFAULT_CONVERSATION
    return cleaned[:64]


def _key(kind: str, conversation: str) -> str:
    if conversation == DEFAULT_CONVERSATION:
        return kind
    return f"{kind}:{conversation}"


def _with_prefs(slots: dict[str, dict[str, str]], prefs: dict[str, str]) -> dict[str, dict[str, str]]:
    filled = merge_slots(empty_slots(), slots)
    city = prefs.get("home_city") or prefs.get("city") or ""
    if city and not filled["weather"]["city"]:
        filled["weather"]["city"] = city
    mode = prefs.get("preferred_mode") or prefs.get("mode") or ""
    if mode and not filled["itinerary"]["mode"]:
        filled["itinerary"]["mode"] = mode
    return filled


class _DictStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], MemoryRecord] = {}

    def upsert(self, record: MemoryRecord) -> None:
        self.rows[(record.tenant_id, record.user_id, record.scope.value, record.key)] = record

    def fetch(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> MemoryRecord | None:
        return self.rows.get((tenant_id, user_id, scope.value, key))

    def list_long_term(self, *, tenant_id: str, user_id: str) -> list[MemoryRecord]:
        return [
            record
            for (owner, user, scope, _key), record in self.rows.items()
            if owner == tenant_id and user == user_id and scope == MemoryScope.LONG_TERM.value
        ]

    def delete(self, *, tenant_id: str, user_id: str, scope: MemoryScope, key: str) -> None:
        self.rows.pop((tenant_id, user_id, scope.value, key), None)


class _NullCache:
    def get(self, key: str) -> str | None:
        del key
        return None

    def put(self, key: str, value: str, *, ttl_seconds: int) -> None:
        del key, value, ttl_seconds

    def delete(self, key: str) -> None:
        del key
