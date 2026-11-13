"""PostgreSQL client (e.g. Amazon RDS): TLS, read-only sessions, small pool, timeouts.

Runs the same SQL templates as Snowflake and the SQLite mock: psycopg uses the
same %(name)s bind syntax, and unquoted identifiers in the schema mapping fold
to lower case, so a mapping such as {"tables": {"providers": "claims_app.providers"}}
is all that changes.

The password comes from PG_PASSWORD or from an RDS-managed Secrets Manager
secret (PG_SECRET_ARN). RDS rotates those secrets, so the value is cached briefly
and re-read when a new connection fails to authenticate.

psycopg is used synchronously; the MCP server already calls execute() from a
bounded thread pool, and this pool caps concurrent database sessions.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import date
from typing import Any

from mcp_server.config import ServerSettings
from mcp_server.db.base import QueryExecutionError, QueryResult
from mcp_server.queries import RenderedQuery

log = logging.getLogger(__name__)

_SECRET_TTL_S = 300
_SQLSTATE_BAD_LOGIN = "28P01"  # invalid_password (an error code, not a secret)
_AUTH_FAILED = "28000"  # invalid_authorization_specification


def _bind_params(params: dict[str, Any]) -> dict[str, Any]:
    """Pass validated ISO date strings as dates so PostgreSQL compares them as DATE."""
    out: dict[str, Any] = {}
    for name, value in params.items():
        if name.endswith("_date") and isinstance(value, str):
            out[name] = date.fromisoformat(value)
        else:
            out[name] = value
    return out


def _is_auth_failure(exc: BaseException) -> bool:
    # Connection-time errors from libpq often carry no sqlstate, only the message.
    if getattr(exc, "sqlstate", None) in (_SQLSTATE_BAD_LOGIN, _AUTH_FAILED):
        return True
    return "password authentication failed" in str(exc).lower()


class PostgresClient:
    def __init__(self, settings: ServerSettings, *, connect: Any = None, secrets_client: Any = None) -> None:
        self._settings = settings
        self._pg = settings.postgres
        self._pool: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=settings.db_pool_size)
        self._slots = threading.BoundedSemaphore(settings.db_pool_size)
        self._connect_fn = connect  # injectable for tests
        self._secrets_client = secrets_client
        self._secret_lock = threading.Lock()
        self._cached_password: str | None = None
        self._cached_at = 0.0

    # ---- credentials -------------------------------------------------------
    def _password(self, *, refresh: bool = False) -> str:
        if self._pg.password:
            return self._pg.password
        with self._secret_lock:
            fresh = time.monotonic() - self._cached_at < _SECRET_TTL_S
            if self._cached_password is not None and fresh and not refresh:
                return self._cached_password
            if self._secrets_client is None:
                import boto3

                self._secrets_client = boto3.client("secretsmanager")
            try:
                resp = self._secrets_client.get_secret_value(SecretId=self._pg.secret_arn)
                password = json.loads(resp["SecretString"])["password"]
            except Exception as exc:  # noqa: BLE001 - never surface secret details
                raise QueryExecutionError(f"could not read database secret: {type(exc).__name__}") from exc
            self._cached_password, self._cached_at = password, time.monotonic()
            return password

    # ---- connections -------------------------------------------------------
    def _connect_kwargs(self, password: str) -> dict[str, Any]:
        timeout_ms = self._settings.statement_timeout_s * 1000
        kwargs: dict[str, Any] = {
            "host": self._pg.host,
            "port": self._pg.port,
            "dbname": self._pg.database,
            "user": self._pg.user,
            "password": password,
            "sslmode": self._pg.sslmode,
            "connect_timeout": self._pg.connect_timeout_s,
            "application_name": "nl-data-assistant",
            # Defense in depth on top of SELECT-only templates and a read-only role.
            "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}",
            "autocommit": True,
        }
        if self._pg.sslrootcert:
            kwargs["sslrootcert"] = self._pg.sslrootcert
        return kwargs

    def _new_connection(self) -> Any:
        if self._connect_fn is None:
            import psycopg
            from psycopg.rows import dict_row

            def connect(**kwargs: Any) -> Any:
                return psycopg.connect(row_factory=dict_row, **kwargs)

            self._connect_fn = connect
        try:
            return self._connect_fn(**self._connect_kwargs(self._password()))
        except Exception as exc:
            if self._pg.password or not _is_auth_failure(exc):
                raise
            # The managed secret may have rotated since we cached it: re-read once.
            log.info("database authentication failed; refreshing secret and retrying")
            return self._connect_fn(**self._connect_kwargs(self._password(refresh=True)))

    def _acquire(self) -> Any:
        try:
            conn = self._pool.get_nowait()
            if not conn.closed:
                return conn
        except queue.Empty:
            pass
        return self._new_connection()

    def _release(self, conn: Any, healthy: bool) -> None:
        if healthy and not conn.closed:
            try:
                self._pool.put_nowait(conn)
                return
            except queue.Full:
                pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 - best effort close
            pass

    # ---- queries -----------------------------------------------------------
    def execute(
        self,
        query: RenderedQuery,
        params: dict[str, Any],
        *,
        row_limit: int,
        query_tag: str,
    ) -> QueryResult:
        query.check_params(params)
        with self._slots:
            healthy = True
            conn = None
            try:
                conn = self._acquire()
                with conn.cursor() as cur:
                    # Visible in pg_stat_activity, so a chat turn can be traced to its query.
                    cur.execute(
                        "SELECT set_config('application_name', %(tag)s, false)",
                        {"tag": f"nl-assistant {query_tag}"[:63]},
                    )
                    cur.execute(query.sql, _bind_params(params))
                    fetched = cur.fetchmany(row_limit + 1)
            except QueryExecutionError:
                raise
            except Exception as exc:
                healthy = False
                # sqlstate only: driver messages can echo bound values.
                raise QueryExecutionError(
                    f"{query.query_id} failed: {type(exc).__name__} sqlstate={getattr(exc, 'sqlstate', None)}"
                ) from exc
            finally:
                if conn is not None:
                    self._release(conn, healthy)
        rows = [dict(r) for r in fetched[:row_limit]]
        return QueryResult(rows=rows, truncated=len(fetched) > row_limit)

    def close(self) -> None:
        while True:
            try:
                self._pool.get_nowait().close()
            except queue.Empty:
                return
            except Exception:  # noqa: BLE001, S112 - best effort close during shutdown
                continue
