"""PostgreSQL client behaviour against a fake psycopg connection (no database needed)."""

import json
from datetime import date

import pytest

from mcp_server.config import PostgresSettings, ServerSettings
from mcp_server.db.base import QueryExecutionError
from mcp_server.db.factory import build_database
from mcp_server.db.postgres_client import PostgresClient
from mcp_server.queries import render_all
from mcp_server.schema_mapping import load_mapping

MAPPING = load_mapping(
    json_text=json.dumps({"tables": {"providers": "claims_app.providers", "claims": "claims_app.claims"}})
)
QUERIES = render_all(MAPPING)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.fail_with and "set_config" not in sql:
            raise self.conn.fail_with

    def fetchmany(self, n):
        return self.conn.rows[:n]


class FakeConn:
    def __init__(self, rows=None, fail_with=None):
        self.rows = rows or []
        self.fail_with = fail_with
        self.executed = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class DbError(Exception):
    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.sqlstate = sqlstate


def _settings(**pg) -> ServerSettings:
    base = {"host": "db.example.internal", "database": "postgres", "user": "app", "password": "pw"}
    base.update(pg)
    return ServerSettings(db_backend="postgres", postgres=PostgresSettings(**base), db_pool_size=2)


def _client(conns, **pg):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        conn = conns.pop(0)
        if isinstance(conn, Exception):
            raise conn
        return conn

    return PostgresClient(_settings(**pg), connect=connect), calls


def test_runs_template_with_date_params_and_tags_session():
    conn = FakeConn(rows=[{"claim_count": 3}])
    client, _ = _client([conn])
    params = {"npi": "1234567893", "start_date": "2025-01-01", "end_date": "2025-12-31"}
    result = client.execute(QUERIES["provider_claim_summary"], params, row_limit=1, query_tag="req-1")
    assert result.rows == [{"claim_count": 3}] and not result.truncated
    (tag_sql, tag_params), (sql, bound) = conn.executed
    assert "set_config('application_name'" in tag_sql and tag_params == {"tag": "nl-assistant req-1"}
    assert sql == QUERIES["provider_claim_summary"].sql and "claims_app.claims" in sql
    assert bound == {"npi": "1234567893", "start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31)}


def test_truncation_detected():
    conn = FakeConn(rows=[{"claim_status": s} for s in "ABC"])
    client, _ = _client([conn])
    params = {"start_date": "2025-01-01", "end_date": "2025-01-31", "row_limit": 3}
    result = client.execute(QUERIES["claim_counts_by_status"], params, row_limit=2, query_tag="t")
    assert len(result.rows) == 2 and result.truncated


def test_connection_is_tls_read_only_and_time_limited():
    client, calls = _client([FakeConn()], sslrootcert="/certs/global-bundle.pem")
    client.execute(
        QUERIES["provider_by_npi"], {"npi": "1234567893", "row_limit": 1}, row_limit=1, query_tag="t"
    )
    kw = calls[0]
    assert kw["sslmode"] == "verify-full" and kw["sslrootcert"] == "/certs/global-bundle.pem"
    assert "default_transaction_read_only=on" in kw["options"]
    assert "statement_timeout=30000" in kw["options"]
    assert kw["autocommit"] is True


def test_connections_are_reused_and_failed_ones_discarded():
    good, bad = FakeConn(), FakeConn(fail_with=DbError("boom with value 1234567893", sqlstate="57014"))
    client, calls = _client([good, bad, FakeConn()])
    q, p = QUERIES["provider_by_npi"], {"npi": "1234567893", "row_limit": 1}
    client.execute(q, p, row_limit=1, query_tag="a")
    client.execute(q, p, row_limit=1, query_tag="b")
    assert len(calls) == 1  # second call reused the pooled connection
    client._pool.get_nowait()  # drop it so the next call connects to `bad`
    with pytest.raises(QueryExecutionError) as err:
        client.execute(q, p, row_limit=1, query_tag="c")
    assert "57014" in str(err.value) and "1234567893" not in str(err.value)
    assert bad.closed


def test_connect_failure_is_safe_error():
    client, _ = _client([DbError("could not connect to server at 10.0.0.5")])
    with pytest.raises(QueryExecutionError) as err:
        client.execute(
            QUERIES["provider_by_npi"], {"npi": "1234567893", "row_limit": 1}, row_limit=1, query_tag="t"
        )
    assert "10.0.0.5" not in str(err.value)


class FakeSecrets:
    def __init__(self, passwords):
        self.passwords = list(passwords)
        self.calls = 0

    def get_secret_value(self, SecretId):  # noqa: N803 - boto3 casing
        self.calls += 1
        return {"SecretString": json.dumps({"username": "postgres", "password": self.passwords.pop(0)})}


def _secret_client(conns, secrets):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs["password"])
        conn = conns.pop(0)
        if isinstance(conn, Exception):
            raise conn
        return conn

    settings = _settings(
        password="", secret_arn="arn:aws:secretsmanager:us-east-1:111122223333:secret:rds!db-x"
    )
    return PostgresClient(settings, connect=connect, secrets_client=secrets), calls


def test_password_read_from_secret_and_cached():
    secrets = FakeSecrets(["s1"])
    client, calls = _secret_client([FakeConn(), FakeConn()], secrets)
    assert client._password() == "s1" and client._password() == "s1"
    assert secrets.calls == 1


def test_rotated_secret_is_refreshed_once_on_auth_failure():
    secrets = FakeSecrets(["old", "new"])
    rejected = DbError('FATAL:  password authentication failed for user "postgres"')
    client, calls = _secret_client([rejected, FakeConn()], secrets)
    client.execute(
        QUERIES["provider_by_npi"], {"npi": "1234567893", "row_limit": 1}, row_limit=1, query_tag="t"
    )
    assert calls == ["old", "new"] and secrets.calls == 2


def test_static_password_is_not_retried():
    client, calls = _client([DbError("password authentication failed", sqlstate="28P01")])
    with pytest.raises(QueryExecutionError):
        client.execute(
            QUERIES["provider_by_npi"], {"npi": "1234567893", "row_limit": 1}, row_limit=1, query_tag="t"
        )
    assert len(calls) == 1


def test_region_comes_from_secret_arn():
    from mcp_server.db.postgres_client import _region_from_arn

    assert _region_from_arn("arn:aws:secretsmanager:us-east-1:111122223333:secret:rds!db-x") == "us-east-1"
    assert _region_from_arn("not-an-arn") is None


def test_secret_errors_do_not_leak_details():
    class Broken:
        def get_secret_value(self, SecretId):  # noqa: N803
            raise RuntimeError("AccessDenied for arn:aws:secretsmanager:...:secret:rds!db-x")

    settings = _settings(password="", secret_arn="arn:x")
    client = PostgresClient(settings, connect=lambda **k: FakeConn(), secrets_client=Broken())
    with pytest.raises(QueryExecutionError) as err:
        client.execute(
            QUERIES["provider_by_npi"], {"npi": "1234567893", "row_limit": 1}, row_limit=1, query_tag="t"
        )
    assert "arn" not in str(err.value)


# ---- configuration -------------------------------------------------------------


def test_backend_defaults_follow_mock_snowflake():
    assert ServerSettings(mock_snowflake=True).backend == "sqlite_mock"
    assert ServerSettings(mock_snowflake=False).backend == "snowflake"
    assert ServerSettings(db_backend="postgres").backend == "postgres"


@pytest.mark.parametrize(
    "pg, message",
    [
        ({"host": ""}, "host"),
        ({"password": "", "secret_arn": ""}, "password or secret_arn"),
        ({"sslmode": "disable"}, "PG_SSLMODE"),
    ],
)
def test_postgres_settings_validated(pg, message):
    with pytest.raises(ValueError, match=message):
        _settings(**pg).validate()


def test_production_requires_verify_full_and_real_db():
    with pytest.raises(ValueError, match="verify-full"):
        ServerSettings(
            environment="production",
            auth_token="t",
            db_backend="postgres",
            postgres=PostgresSettings(host="h", database="d", user="u", password="p", sslmode="require"),
        ).validate()
    with pytest.raises(ValueError, match="real database"):
        ServerSettings(environment="production", auth_token="t", db_backend="sqlite_mock").validate()


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="DB_BACKEND"):
        ServerSettings(db_backend="mysql").validate()


def test_from_env_reads_postgres_settings(monkeypatch):
    env = {
        "DB_BACKEND": "postgres",
        "PG_HOST": "npi.example.rds.amazonaws.com",
        "PG_DATABASE": "postgres",
        "PG_USER": "postgres",
        "PG_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:x",
        "PG_SSLROOTCERT": "/certs/global-bundle.pem",
        "DB_POOL_SIZE": "3",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = ServerSettings.from_env()
    assert s.backend == "postgres" and s.db_pool_size == 3
    assert s.postgres.host == env["PG_HOST"] and s.postgres.sslmode == "verify-full"
    assert s.postgres.password == ""  # comes from the secret at connect time


def test_factory_builds_postgres_client_without_connecting():
    client, mapping = build_database(_settings(), MAPPING)
    assert isinstance(client, PostgresClient) and mapping is MAPPING


def test_unquoted_mapping_renders_for_postgres():
    sql = QUERIES["provider_by_npi"].sql
    assert "FROM claims_app.providers" in sql and "%(npi)s" in sql
