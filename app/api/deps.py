from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.agent.bedrock_agent import BedrockAgent
from app.agent.tool_registry import ToolRegistry
from app.api.errors import ApiError
from app.auth.alb_oidc import AlbOidcVerifier, AuthError
from app.auth.user import User
from app.core.config import Settings
from app.storage.base import ConversationStore


@dataclass
class Services:
    settings: Settings
    agent: BedrockAgent
    registry: ToolRegistry
    store: ConversationStore
    request_slots: asyncio.Semaphore
    verifier: AlbOidcVerifier | None = None
    closers: tuple[Any, ...] = ()


def get_services(request: Request) -> Services:
    return request.app.state.services


async def get_current_user(request: Request) -> User:
    services: Services = request.app.state.services
    if services.settings.auth_mode == "none":
        return User(sub="local-anonymous")
    assert services.verifier is not None
    try:
        return await services.verifier.verify(request.headers.get("x-amzn-oidc-data"))
    except AuthError as exc:
        raise ApiError(401, "unauthorized", "Sign in to use the assistant.") from exc
