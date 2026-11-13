from __future__ import annotations

from mcp_server.config import ServerSettings
from mcp_server.db.base import DatabaseClient
from mcp_server.schema_mapping import SchemaMapping


def build_database(settings: ServerSettings, mapping: SchemaMapping) -> tuple[DatabaseClient, SchemaMapping]:
    """Return the client and the mapping it actually uses."""
    if settings.mock_snowflake:
        from mcp_server.db.sqlite_mock import SqliteMockClient

        client = SqliteMockClient(mapping)
        return client, client.mapping
    from mcp_server.db.snowflake_client import SnowflakeClient

    return SnowflakeClient(settings), mapping
