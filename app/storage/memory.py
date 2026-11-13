"""In-process store. LOCAL DEVELOPMENT AND TESTS ONLY.

State is lost on restart and not shared across tasks, so the app refuses to
use it when ENVIRONMENT=production.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.agent.types import Turn
from app.storage.base import LoadStatus


@dataclass
class _Entry:
    owner: str
    turns: list[Turn] = field(default_factory=list)
    expires_at: float = 0.0


class InMemoryConversationStore:
    def __init__(self, ttl_s: int = 86_400, max_conversations: int = 5_000) -> None:
        self._ttl = ttl_s
        self._max = max_conversations
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def load(self, conversation_id: str, owner: str) -> tuple[LoadStatus, list[Turn]]:
        async with self._lock:
            entry = self._data.get(conversation_id)
            if entry is None or entry.expires_at < time.monotonic():
                self._data.pop(conversation_id, None)
                return LoadStatus.MISSING, []
            if entry.owner != owner:
                return LoadStatus.FORBIDDEN, []
            self._data.move_to_end(conversation_id)
            return LoadStatus.OK, list(entry.turns)

    async def append(self, conversation_id: str, owner: str, turns: list[Turn], *, max_turns: int) -> None:
        async with self._lock:
            entry = self._data.get(conversation_id)
            if entry is None or entry.expires_at < time.monotonic():
                entry = _Entry(owner=owner)
            elif entry.owner != owner:
                raise PermissionError("conversation owned by another user")
            entry.turns = (entry.turns + turns)[-2 * max_turns :]
            entry.expires_at = time.monotonic() + self._ttl
            self._data[conversation_id] = entry
            self._data.move_to_end(conversation_id)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    async def ping(self) -> bool:
        return True
