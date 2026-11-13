"""DynamoDB conversation store for deployed environments.

Table: partition key `conversation_id` (S), TTL attribute `expires_at` (N),
server-side encryption with a customer-managed KMS key (see infra/terraform).
Writes are conditional on ownership.
"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agent.types import Turn
from app.storage.base import LoadStatus

_MAX_ITEM_CHARS = 300_000  # stay well under DynamoDB's 400 KB item limit


class DynamoDBConversationStore:
    def __init__(self, table_name: str, region: str, ttl_s: int, max_workers: int = 8) -> None:
        import boto3
        from botocore.config import Config

        self._table = boto3.resource(
            "dynamodb",
            config=Config(
                region_name=region,
                retries={"mode": "standard", "max_attempts": 3},
                connect_timeout=3,
                read_timeout=5,
            ),
        ).Table(table_name)
        self._ttl = ttl_s
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ddb")

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: fn(*args, **kwargs))

    async def load(self, conversation_id: str, owner: str) -> tuple[LoadStatus, list[Turn]]:
        resp = await self._run(
            self._table.get_item, Key={"conversation_id": conversation_id}, ConsistentRead=True
        )
        item = resp.get("Item")
        if not item or int(item.get("expires_at", 0)) < int(time.time()):
            return LoadStatus.MISSING, []
        if item.get("owner") != owner:
            return LoadStatus.FORBIDDEN, []
        turns = [Turn(role=t["role"], text=t["text"]) for t in json.loads(item.get("turns", "[]"))]
        return LoadStatus.OK, turns

    async def append(self, conversation_id: str, owner: str, turns: list[Turn], *, max_turns: int) -> None:
        from botocore.exceptions import ClientError

        status, existing = await self.load(conversation_id, owner)
        if status is LoadStatus.FORBIDDEN:
            raise PermissionError("conversation owned by another user")
        combined = (existing + turns)[-2 * max_turns :]
        payload = json.dumps([{"role": t.role, "text": t.text} for t in combined])
        while len(payload) > _MAX_ITEM_CHARS and len(combined) > 2:
            combined = combined[2:]
            payload = json.dumps([{"role": t.role, "text": t.text} for t in combined])
        try:
            await self._run(
                self._table.put_item,
                Item={
                    "conversation_id": conversation_id,
                    "owner": owner,
                    "turns": payload,
                    "expires_at": int(time.time()) + self._ttl,
                },
                ConditionExpression="attribute_not_exists(conversation_id) OR #o = :owner",
                ExpressionAttributeNames={"#o": "owner"},
                ExpressionAttributeValues={":owner": owner},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise PermissionError("conversation owned by another user") from exc
            raise

    async def ping(self) -> bool:
        return True  # avoid a network call per readiness probe
