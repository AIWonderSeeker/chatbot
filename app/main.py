"""FastAPI app factory and dependency wiring."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.bedrock_agent import AgentSettings, BedrockAgent
from app.agent.errors import ToolsUnavailableError
from app.agent.tool_registry import ToolRegistry
from app.agent.types import ConverseClient, ToolExecutor
from app.api.deps import Services
from app.api.errors import install_error_handlers
from app.api.routes import chat, health
from app.auth.alb_oidc import AlbOidcVerifier
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.storage.base import ConversationStore

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def build_services(
    settings: Settings,
    *,
    model: ConverseClient | None = None,
    executor: ToolExecutor | None = None,
    store: ConversationStore | None = None,
) -> Services:
    closers: list[Any] = []
    if model is None:
        if settings.mock_bedrock:
            from app.agent.fake_bedrock import FakeBedrockClient

            model = FakeBedrockClient()
        else:
            from app.agent.bedrock_client import BedrockConverseClient

            model = BedrockConverseClient(
                region=settings.aws_region,
                model_id=settings.bedrock_model_id,
                max_tokens=settings.bedrock_max_tokens,
                temperature=settings.bedrock_temperature,
                connect_timeout_s=settings.bedrock_connect_timeout_seconds,
                read_timeout_s=settings.bedrock_read_timeout_seconds,
                max_attempts=settings.bedrock_max_attempts,
                max_concurrency=settings.bedrock_max_concurrency,
            )
            closers.append(model)
    if executor is None:
        from app.mcp_client.client import McpToolExecutor

        executor = McpToolExecutor(
            settings.mcp_server_url,
            settings.mcp_auth_token.get_secret_value(),
            settings.mcp_timeout_seconds,
        )
    if store is None:
        if settings.conversation_store == "dynamodb":
            from app.storage.dynamodb import DynamoDBConversationStore

            store = DynamoDBConversationStore(
                settings.dynamodb_table, settings.aws_region, settings.conversation_ttl_seconds
            )
        else:
            from app.storage.memory import InMemoryConversationStore

            store = InMemoryConversationStore(ttl_s=settings.conversation_ttl_seconds)

    registry = ToolRegistry(executor, settings.allowlist, cache_ttl_s=settings.tool_cache_ttl_seconds)
    agent = BedrockAgent(
        model=model,
        registry=registry,
        executor=executor,
        store=store,
        settings=AgentSettings(
            max_tool_rounds=settings.max_tool_rounds,
            max_parallel_tools=settings.max_parallel_tools,
            tool_timeout_s=settings.tool_timeout_seconds,
            model_timeout_s=settings.model_call_timeout_seconds,
            max_history_turns=settings.max_history_turns,
        ),
    )
    verifier = (
        AlbOidcVerifier(settings.aws_region, settings.alb_arn) if settings.auth_mode == "alb_oidc" else None
    )
    return Services(
        settings=settings,
        agent=agent,
        registry=registry,
        store=store,
        request_slots=asyncio.Semaphore(settings.max_concurrent_requests),
        verifier=verifier,
        closers=tuple(closers),
    )


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or (services.settings if services else get_settings())
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = services or build_services(settings)
        try:
            await app.state.services.registry.ensure_loaded()
        except ToolsUnavailableError:
            log.warning("MCP server not reachable at startup; will retry on first request")
        log.info(
            "startup complete",
            extra={"environment": settings.environment, "mock_bedrock": settings.mock_bedrock},
        )
        yield
        for closer in app.state.services.closers:
            closer.close()

    app = FastAPI(
        title="Claims Data Assistant",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "production" else "/openapi.json",
    )
    install_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Request-ID"],
            allow_credentials=False,
        )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
