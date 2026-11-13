"""Builds the ToolContext from settings. Shared by the server and tests."""

from __future__ import annotations

from mcp_server.config import ServerSettings
from mcp_server.db.factory import build_database
from mcp_server.queries import render_all
from mcp_server.schema_mapping import load_mapping
from mcp_server.tools.context import ToolContext


def build_context(settings: ServerSettings) -> ToolContext:
    mapping = load_mapping(settings.schema_mapping_path or None, settings.schema_mapping_json or None)
    db, effective_mapping = build_database(settings, mapping)
    return ToolContext(
        db=db,
        queries=render_all(effective_mapping),
        max_rows=settings.max_rows,
        max_date_range_days=settings.max_date_range_days,
        small_cell_threshold=settings.small_cell_threshold,
    )
