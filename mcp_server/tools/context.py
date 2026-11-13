from __future__ import annotations

from dataclasses import dataclass

from mcp_server.db.base import DatabaseClient
from mcp_server.queries import RenderedQuery


@dataclass(frozen=True)
class ToolContext:
    db: DatabaseClient
    queries: dict[str, RenderedQuery]
    max_rows: int
    max_date_range_days: int
    small_cell_threshold: int
