from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.agent.errors import AgentError
from app.api.deps import Services, get_current_user, get_services
from app.api.errors import ApiError
from app.auth.user import User
from app.core.logging import request_id_var
from app.schemas.chat import ChatRequest, ChatResponse, ErrorResponse, ToolCallSummary

log = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    services: Services = Depends(get_services),
) -> ChatResponse:
    settings = services.settings
    if len(body.message) > settings.max_message_chars:
        raise ApiError(
            422, "message_too_long", f"Keep questions under {settings.max_message_chars} characters."
        )
    if services.request_slots.locked():
        raise ApiError(503, "busy", "The assistant is handling too many requests. Try again shortly.")

    async with services.request_slots:
        try:
            result = await services.agent.process(
                body.message,
                body.conversation_id,
                user.sub,
                budget_s=settings.request_timeout_seconds,
            )
        except AgentError as exc:
            log.warning("agent error", extra={"error_code": exc.code, "error_type": type(exc).__name__})
            raise ApiError(exc.status_code, exc.code, exc.user_message) from exc

    return ChatResponse(
        answer=result.answer,
        conversation_id=result.conversation_id,
        request_id=request_id_var.get(),
        tool_calls=[
            ToolCallSummary(name=r.name, status=r.status, duration_ms=r.duration_ms)
            for r in result.tool_calls
        ],
    )
