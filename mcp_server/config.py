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
            db_pool_size=_int("SNOWFLAKE_POOL_SIZE", 4),
            db_thread_workers=_int("DB_THREAD_WORKERS", 8),
            snowflake=sf,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.max_rows < 1 or self.max_rows > 5000:
            raise ValueError("MAX_ROWS must be between 1 and 5000")
        if self.environment == "production":
            if self.mock_snowflake:
                raise ValueError("MOCK_SNOWFLAKE must be false in production")
            if not self.auth_token:
                raise ValueError("MCP_AUTH_TOKEN is required in production")
        if not self.mock_snowflake:
            missing = [
                n
                for n in ("account", "user", "role", "warehouse", "database", "schema")
                if not getattr(self.snowflake, n)
            ]
            if missing:
                raise ValueError(f"Missing Snowflake settings: {', '.join(missing)}")
