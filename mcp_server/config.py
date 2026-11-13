"""MCP server settings, read from environment variables.

Kept dependency-free (no pydantic-settings) so the data layer can be imported
and tested without the web stack installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class SnowflakeSettings:
    account: str = ""
    user: str = ""
    role: str = ""
    warehouse: str = ""
    database: str = ""
    schema: str = ""
    # keypair (recommended) | password (local experiments only) | oauth
    auth_method: str = "keypair"
    private_key_path: str = ""
    private_key_pem: str = field(default="", repr=False)  # injected from Secrets Manager
    private_key_passphrase: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    oauth_token: str = field(default="", repr=False)
    login_timeout_s: int = 15
    network_timeout_s: int = 30


@dataclass(frozen=True)
class PostgresSettings:
    """PostgreSQL (e.g. Amazon RDS) connection settings.

    The password comes from PG_PASSWORD, or from an RDS-managed Secrets Manager
    secret (PG_SECRET_ARN) that is re-read when it may have rotated.
    """

    host: str = ""
    port: int = 5432
    database: str = ""
    user: str = ""
    password: str = field(default="", repr=False)
    secret_arn: str = ""
    sslmode: str = "verify-full"
    sslrootcert: str = ""
    connect_timeout_s: int = 10


DB_BACKENDS = ("sqlite_mock", "snowflake", "postgres")


@dataclass(frozen=True)
class ServerSettings:
    environment: str = "local"
    host: str = "0.0.0.0"  # noqa: S104 - container must listen on all interfaces
    port: int = 8001
    log_level: str = "INFO"
    mock_snowflake: bool = True
    auth_token: str = field(default="", repr=False)
    schema_mapping_path: str = ""
    schema_mapping_json: str = field(default="", repr=False)
    max_rows: int = 200
    statement_timeout_s: int = 30
    max_date_range_days: int = 731
    small_cell_threshold: int = 11
    db_pool_size: int = 4
    db_thread_workers: int = 8
    snowflake: SnowflakeSettings = field(default_factory=SnowflakeSettings)
    # sqlite_mock | snowflake | postgres. Empty = derive from MOCK_SNOWFLAKE (backward compatible).
    db_backend: str = ""
    postgres: PostgresSettings = field(default_factory=PostgresSettings)

    @property
    def backend(self) -> str:
        if self.db_backend:
            return self.db_backend
        return "sqlite_mock" if self.mock_snowflake else "snowflake"

    @classmethod
    def from_env(cls) -> ServerSettings:
        sf = SnowflakeSettings(
            account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
            user=os.getenv("SNOWFLAKE_USER", ""),
            role=os.getenv("SNOWFLAKE_ROLE", ""),
            warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", ""),
            database=os.getenv("SNOWFLAKE_DATABASE", ""),
            schema=os.getenv("SNOWFLAKE_SCHEMA", ""),
            auth_method=os.getenv("SNOWFLAKE_AUTH_METHOD", "keypair").lower(),
            private_key_path=os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", ""),
            private_key_pem=os.getenv("SNOWFLAKE_PRIVATE_KEY_PEM", ""),
            private_key_passphrase=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", ""),
            password=os.getenv("SNOWFLAKE_PASSWORD", ""),
            oauth_token=os.getenv("SNOWFLAKE_OAUTH_TOKEN", ""),
            login_timeout_s=_int("SNOWFLAKE_LOGIN_TIMEOUT_SECONDS", 15),
            network_timeout_s=_int("SNOWFLAKE_NETWORK_TIMEOUT_SECONDS", 30),
        )
        pg = PostgresSettings(
            host=os.getenv("PG_HOST", ""),
            port=_int("PG_PORT", 5432),
            database=os.getenv("PG_DATABASE", ""),
            user=os.getenv("PG_USER", ""),
            password=os.getenv("PG_PASSWORD", ""),
            secret_arn=os.getenv("PG_SECRET_ARN", ""),
            sslmode=os.getenv("PG_SSLMODE", "verify-full"),
            sslrootcert=os.getenv("PG_SSLROOTCERT", ""),
            connect_timeout_s=_int("PG_CONNECT_TIMEOUT_SECONDS", 10),
        )
        settings = cls(
            environment=os.getenv("ENVIRONMENT", "local").lower(),
            host=os.getenv("MCP_HOST", "0.0.0.0"),  # noqa: S104 - see above
            port=_int("MCP_PORT", 8001),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            mock_snowflake=_bool("MOCK_SNOWFLAKE", True),
            auth_token=os.getenv("MCP_AUTH_TOKEN", ""),
            schema_mapping_path=os.getenv("SCHEMA_MAPPING_PATH", ""),
            schema_mapping_json=os.getenv("SCHEMA_MAPPING_JSON", ""),
            max_rows=_int("MAX_ROWS", 200),
            statement_timeout_s=_int("STATEMENT_TIMEOUT_SECONDS", 30),
            max_date_range_days=_int("MAX_DATE_RANGE_DAYS", 731),
            small_cell_threshold=_int("SMALL_CELL_THRESHOLD", 11),
            db_pool_size=_int("DB_POOL_SIZE", _int("SNOWFLAKE_POOL_SIZE", 4)),
            db_thread_workers=_int("DB_THREAD_WORKERS", 8),
            snowflake=sf,
            db_backend=os.getenv("DB_BACKEND", "").strip().lower(),
            postgres=pg,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.max_rows < 1 or self.max_rows > 5000:
            raise ValueError("MAX_ROWS must be between 1 and 5000")
        if self.backend not in DB_BACKENDS:
            raise ValueError(f"DB_BACKEND must be one of {', '.join(DB_BACKENDS)}")
        if self.environment == "production":
            if self.backend == "sqlite_mock":
                raise ValueError("A real database (DB_BACKEND) is required in production")
            if not self.auth_token:
                raise ValueError("MCP_AUTH_TOKEN is required in production")
        if self.backend == "postgres":
            pg = self.postgres
            missing = [n for n in ("host", "database", "user") if not getattr(pg, n)]
            if not (pg.password or pg.secret_arn):
                missing.append("password or secret_arn")
            if missing:
                raise ValueError(f"Missing PostgreSQL settings: {', '.join(missing)}")
            if pg.sslmode not in ("require", "verify-ca", "verify-full"):
                raise ValueError("PG_SSLMODE must be require, verify-ca, or verify-full")
            if self.environment == "production" and pg.sslmode != "verify-full":
                raise ValueError("PG_SSLMODE must be verify-full in production")
        if self.backend == "snowflake":
            missing = [
                n
                for n in ("account", "user", "role", "warehouse", "database", "schema")
                if not getattr(self.snowflake, n)
            ]
            if missing:
                raise ValueError(f"Missing Snowflake settings: {', '.join(missing)}")
