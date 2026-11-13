"""Snowflake client: key-pair auth, small connection pool, timeouts, query tags.

snowflake-connector-python is synchronous. The MCP server calls execute()
from a bounded thread pool, and this pool caps concurrent warehouse sessions.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from mcp_server.config import ServerSettings
from mcp_server.db.base import QueryExecutionError, QueryResult
from mcp_server.queries import RenderedQuery

log = logging.getLogger(__name__)


def _load_private_key_der(pem: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(pem, password=passphrase.encode() or None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class SnowflakeClient:
    def __init__(self, settings: ServerSettings) -> None:
        self._settings = settings
        self._pool: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=settings.db_pool_size)
        self._slots = threading.BoundedSemaphore(settings.db_pool_size)
        self._private_key: bytes | None = None

    def _connect_kwargs(self) -> dict[str, Any]:
        sf = self._settings.snowflake
        kwargs: dict[str, Any] = {
            "account": sf.account,
            "user": sf.user,
            "role": sf.role,
            "warehouse": sf.warehouse,
            "database": sf.database,
            "schema": sf.schema,
            "application": "nl-data-assistant",
            "login_timeout": sf.login_timeout_s,
            "network_timeout": sf.network_timeout_s,
            "client_session_keep_alive": False,
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": self._settings.statement_timeout_s,
                "QUERY_TAG": "nl-data-assistant",
            },
        }
        if sf.auth_method == "keypair":
            if self._private_key is None:
                if sf.private_key_pem:
                    pem = sf.private_key_pem.encode()
                elif sf.private_key_path:
                    with open(sf.private_key_path, "rb") as fh:
                        pem = fh.read()
                else:
                    raise QueryExecutionError("Key-pair auth selected but no private key configured")
                self._private_key = _load_private_key_der(pem, sf.private_key_passphrase)
            kwargs["private_key"] = self._private_key
        elif sf.auth_method == "oauth":
            kwargs["authenticator"] = "oauth"
            kwargs["token"] = sf.oauth_token
        elif sf.auth_method == "password":
            if self._settings.environment == "production":
                raise QueryExecutionError("Password auth is not allowed in production")
            kwargs["password"] = sf.password
        else:
            raise QueryExecutionError(f"Unknown SNOWFLAKE_AUTH_METHOD {sf.auth_method!r}")
        return kwargs

    def _acquire(self) -> Any:
        import snowflake.connector

        try:
            conn = self._pool.get_nowait()
            if not conn.is_closed():
                return conn
        except queue.Empty:
            pass
        return snowflake.connector.connect(**self._connect_kwargs())

    def _release(self, conn: Any, healthy: bool) -> None:
        if healthy and not conn.is_closed():
            try:
                self._pool.put_nowait(conn)
                return
            except queue.Full:
                pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best effort close
            pass

    def execute(
        self,
        query: RenderedQuery,
        params: dict[str, Any],
        *,
        row_limit: int,
        query_tag: str,
    ) -> QueryResult:
        from snowflake.connector import DictCursor
        from snowflake.connector.errors import Error as SnowflakeError

        query.check_params(params)
        with self._slots:
            conn = self._acquire()
            healthy = True
            try:
                with conn.cursor(DictCursor) as cur:
                    # Client-side binding with the connector's escaping (pyformat).
                    cur.execute("ALTER SESSION SET QUERY_TAG = %(tag)s", {"tag": query_tag[:2000]})
                    cur.execute(query.sql, params, timeout=self._settings.statement_timeout_s)
                    fetched = cur.fetchmany(row_limit + 1)
            except SnowflakeError as exc:
                healthy = False
                # errno/sqlstate only: the message can echo bound values.
                raise QueryExecutionError(
                    f"{query.query_id} failed: errno={getattr(exc, 'errno', None)} "
                    f"sqlstate={getattr(exc, 'sqlstate', None)}"
                ) from exc
            finally:
                self._release(conn, healthy)
        return QueryResult(rows=list(fetched[:row_limit]), truncated=len(fetched) > row_limit)

    def close(self) -> None:
        while True:
            try:
                self._pool.get_nowait().close()
            except queue.Empty:
                return
            except Exception:  # noqa: BLE001, S112 - best effort close during shutdown
                continue
