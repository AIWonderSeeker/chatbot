import asyncio

import pytest

from app.agent.types import Turn
from app.storage.base import LoadStatus
from app.storage.memory import InMemoryConversationStore


def test_memory_store_ownership_and_trimming():
    async def scenario():
        store = InMemoryConversationStore()
        assert (await store.load("c1", "u1"))[0] is LoadStatus.MISSING
        for i in range(5):
            await store.append("c1", "u1", [Turn("user", f"q{i}"), Turn("assistant", f"a{i}")], max_turns=2)
        status, turns = await store.load("c1", "u1")
        assert status is LoadStatus.OK and [t.text for t in turns] == ["q3", "a3", "q4", "a4"]
        assert (await store.load("c1", "u2"))[0] is LoadStatus.FORBIDDEN
        with pytest.raises(PermissionError):
            await store.append("c1", "u2", [Turn("user", "x")], max_turns=2)

    asyncio.run(scenario())


def test_memory_store_evicts_oldest():
    async def scenario():
        store = InMemoryConversationStore(max_conversations=2)
        for cid in ("a", "b", "c"):
            await store.append(cid, "u", [Turn("user", cid)], max_turns=5)
        assert (await store.load("a", "u"))[0] is LoadStatus.MISSING
        assert (await store.load("c", "u"))[0] is LoadStatus.OK

    asyncio.run(scenario())
