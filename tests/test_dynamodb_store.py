"""DynamoDB conversation store against an in-process fake table (no AWS calls)."""

import asyncio
import json
import time

import pytest
from botocore.exceptions import ClientError

from app.agent.types import Turn
from app.storage import dynamodb
from app.storage.base import LoadStatus
from app.storage.dynamodb import DynamoDBConversationStore


class FakeTable:
    """Implements the get_item/put_item subset the store uses, including the owner condition."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.put_calls: list[dict] = []
        self.fail_with: str | None = None

    def get_item(self, Key, ConsistentRead=False):  # noqa: N803 - boto3 casing
        item = self.items.get(Key["conversation_id"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):  # noqa: N803
        self.put_calls.append(
            {
                "Item": Item,
                "ConditionExpression": ConditionExpression,
                "ExpressionAttributeNames": ExpressionAttributeNames,
                "ExpressionAttributeValues": ExpressionAttributeValues,
            }
        )
        if self.fail_with:
            raise ClientError({"Error": {"Code": self.fail_with, "Message": "x"}}, "PutItem")
        existing = self.items.get(Item["conversation_id"])
        if existing and existing["owner"] != ExpressionAttributeValues[":owner"]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "PutItem"
            )
        self.items[Item["conversation_id"]] = dict(Item)


@pytest.fixture
def store_and_table():
    store = DynamoDBConversationStore(table_name="conversations", region="us-east-1", ttl_s=3600)
    table = FakeTable()
    store._table = table
    return store, table


def _pair(i: int) -> list[Turn]:
    return [Turn("user", f"q{i}"), Turn("assistant", f"a{i}")]


def test_missing_then_round_trip(store_and_table):
    store, table = store_and_table

    async def scenario():
        assert await store.load("c1", "u1") == (LoadStatus.MISSING, [])
        await store.append("c1", "u1", _pair(0), max_turns=5)
        status, turns = await store.load("c1", "u1")
        assert status is LoadStatus.OK and [t.text for t in turns] == ["q0", "a0"]

    asyncio.run(scenario())
    item = table.items["c1"]
    assert item["owner"] == "u1"
    assert time.time() + 3500 < item["expires_at"] <= time.time() + 3600
    # Only text turns are persisted.
    assert json.loads(item["turns"]) == [{"role": "user", "text": "q0"}, {"role": "assistant", "text": "a0"}]


def test_writes_are_conditional_on_owner(store_and_table):
    store, table = store_and_table
    asyncio.run(store.append("c1", "u1", _pair(0), max_turns=5))
    call = table.put_calls[-1]
    assert call["ConditionExpression"] == "attribute_not_exists(conversation_id) OR #o = :owner"
    assert call["ExpressionAttributeNames"] == {"#o": "owner"}
    assert call["ExpressionAttributeValues"] == {":owner": "u1"}


def test_history_trimmed_to_max_turns(store_and_table):
    store, _ = store_and_table

    async def scenario():
        for i in range(5):
            await store.append("c1", "u1", _pair(i), max_turns=2)
        return await store.load("c1", "u1")

    status, turns = asyncio.run(scenario())
    assert status is LoadStatus.OK and [t.text for t in turns] == ["q3", "a3", "q4", "a4"]


def test_other_user_is_forbidden(store_and_table):
    store, _ = store_and_table

    async def scenario():
        await store.append("c1", "u1", _pair(0), max_turns=5)
        assert (await store.load("c1", "u2")) == (LoadStatus.FORBIDDEN, [])
        with pytest.raises(PermissionError):
            await store.append("c1", "u2", _pair(1), max_turns=5)

    asyncio.run(scenario())


def test_expired_item_treated_as_missing(store_and_table):
    store, table = store_and_table
    table.items["old"] = {
        "conversation_id": "old",
        "owner": "u1",
        "turns": json.dumps([{"role": "user", "text": "q"}]),
        "expires_at": int(time.time()) - 1,
    }
    assert asyncio.run(store.load("old", "u1")) == (LoadStatus.MISSING, [])


def test_race_lost_on_condition_becomes_permission_error(store_and_table):
    # Another user created the conversation between our read and our write.
    store, table = store_and_table
    table.fail_with = "ConditionalCheckFailedException"
    with pytest.raises(PermissionError):
        asyncio.run(store.append("c1", "u1", _pair(0), max_turns=5))


def test_other_client_errors_propagate(store_and_table):
    store, table = store_and_table
    table.fail_with = "ProvisionedThroughputExceededException"
    with pytest.raises(ClientError):
        asyncio.run(store.append("c1", "u1", _pair(0), max_turns=5))


def test_oversized_history_drops_oldest_pairs(store_and_table, monkeypatch):
    store, table = store_and_table
    monkeypatch.setattr(dynamodb, "_MAX_ITEM_CHARS", 200)
    big = "x" * 60

    async def scenario():
        for i in range(4):
            await store.append(
                "c1", "u1", [Turn("user", f"{i}{big}"), Turn("assistant", f"{i}")], max_turns=10
            )

    asyncio.run(scenario())
    stored = json.loads(table.items["c1"]["turns"])
    assert len(stored) < 8  # older pairs were dropped to fit the size limit
    assert stored[-1] == {"role": "assistant", "text": "3"}
    assert len(stored) % 2 == 0


def test_ping_makes_no_network_call(store_and_table):
    store, _ = store_and_table
    assert asyncio.run(store.ping()) is True
