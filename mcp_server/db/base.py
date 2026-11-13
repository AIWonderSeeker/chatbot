"""Database client interface shared by Snowflake and the SQLite mock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from mcp_server.queries import RenderedQuery


class QueryExecutionError(RuntimeError):
    """Execution failed. The message is internal; never return it to clients."""


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    truncated: bool


class DatabaseClient(Protocol):
    def execute(
        self,
        query: RenderedQuery,
        params: dict[str, Any],
        *,
        row_limit: int,
        query_tag: str,
    ) -> QueryResult: ...

    def close(self) -> None: ...
