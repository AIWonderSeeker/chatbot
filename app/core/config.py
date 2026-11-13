from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "dev", "staging", "production"] = "local"
    log_level: str = "INFO"

    # Bedrock
    mock_bedrock: bool = True
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "REPLACE_WITH_MODEL_OR_INFERENCE_PROFILE_ID"
    bedrock_max_tokens: int = Field(1024, ge=64, le=8192)
    bedrock_temperature: float = Field(0.0, ge=0.0, le=1.0)
    bedrock_connect_timeout_seconds: float = 5.0
    bedrock_read_timeout_seconds: float = 60.0
    bedrock_max_attempts: int = Field(4, ge=1, le=10)
    bedrock_max_concurrency: int = Field(16, ge=1, le=256)

    # MCP
    mcp_server_url: str = "http://localhost:8001/mcp"
    mcp_auth_token: SecretStr = SecretStr("")
    mcp_timeout_seconds: float = 30.0
    tool_allowlist: str = "get_provider_by_npi,get_provider_claim_summary,get_claim_counts_by_status"
    tool_cache_ttl_seconds: float = 300.0

    # Agent and request limits
    max_tool_rounds: int = Field(5, ge=1, le=20)
    max_parallel_tools: int = Field(4, ge=1, le=16)
    tool_timeout_seconds: float = 20.0
    model_call_timeout_seconds: float = 45.0
    request_timeout_seconds: float = 90.0
    max_message_chars: int = Field(2000, ge=1, le=4000)
    max_concurrent_requests: int = Field(64, ge=1)

    # Conversation storage
    conversation_store: Literal["memory", "dynamodb"] = "memory"
    dynamodb_table: str = ""
    conversation_ttl_seconds: int = 86_400
    max_history_turns: int = Field(10, ge=0, le=50)

    # Auth and CORS
    auth_mode: Literal["none", "alb_oidc"] = "none"
    alb_arn: str = ""  # expected signer of x-amzn-oidc-data
    cors_allowed_origins: str = ""  # comma-separated; empty = same-origin only

    @property
    def allowlist(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.tool_allowlist.split(",") if n.strip())

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.environment == "production":
            problems = []
            if self.auth_mode == "none":
                problems.append("AUTH_MODE must not be 'none'")
            if self.conversation_store == "memory":
                problems.append("CONVERSATION_STORE must be a shared store")
            if self.mock_bedrock:
                problems.append("MOCK_BEDROCK must be false")
            if not self.mcp_auth_token.get_secret_value():
                problems.append("MCP_AUTH_TOKEN is required")
            if "*" in self.cors_origins:
                problems.append("CORS_ALLOWED_ORIGINS must not be '*'")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        if self.auth_mode == "alb_oidc" and not self.alb_arn:
            raise ValueError("ALB_ARN is required when AUTH_MODE=alb_oidc")
        if self.conversation_store == "dynamodb" and not self.dynamodb_table:
            raise ValueError("DYNAMODB_TABLE is required when CONVERSATION_STORE=dynamodb")
        if not self.mock_bedrock and self.bedrock_model_id.startswith("REPLACE_"):
            raise ValueError("BEDROCK_MODEL_ID must be set when MOCK_BEDROCK=false")
        if self.model_call_timeout_seconds >= self.request_timeout_seconds:
            raise ValueError("MODEL_CALL_TIMEOUT_SECONDS must be < REQUEST_TIMEOUT_SECONDS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
