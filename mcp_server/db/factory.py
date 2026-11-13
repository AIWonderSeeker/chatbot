from __future__ import annotations

from mcp_server.config import ServerSettings
from mcp_server.db.base import DatabaseClient
from mcp_server.schema_mapping import SchemaMapping


def build_database(settings: ServerSettings, mapping: SchemaMapping) -> tuple[DatabaseClient, SchemaMapping]:
    """Return the client and the mapping it actually uses."""
    if settings.backend == "sqlite_mock":
        from mcp_server.db.sqlite_mock import SqliteMockClient

        client = SqliteMockClient(mapping)
        return client, client.mapping
    if settings.backend == "postgres":
        from mcp_server.db.postgres_client import PostgresClient

        return PostgresClient(settings), mapping
    from mcp_server.db.snowflake_client import SnowflakeClient

    return SnowflakeClient(settings), mapping
