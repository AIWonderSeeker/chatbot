"""Conversation storage abstraction.

Only user text and final assistant text are persisted (never tool results), and
each conversation is bound to its owner.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from app.agent.types import Turn


class LoadStatus(Enum):
    OK = "ok"
    MISSING = "missing"
    FORBIDDEN = "forbidden"


class ConversationStore(Protocol):
    async def load(self, conversation_id: str, owner: str) -> tuple[LoadStatus, list[Turn]]: ...

    async def append(
        self, conversation_id: str, owner: str, turns: list[Turn], *, max_turns: int
    ) -> None: ...

    async def ping(self) -> bool: ...
