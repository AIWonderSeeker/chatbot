from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None

    @field_validator("conversation_id")
    @classmethod
    def _valid_conversation_id(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        value = value.lower()
        if not _UUID_RE.fullmatch(value):
            raise ValueError("conversation_id must be a UUID")
        return value


class ToolCallSummary(BaseModel):
    name: str
    status: Literal["success", "error", "rejected", "timeout"]
    duration_ms: int


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    request_id: str
    tool_calls: list[ToolCallSummary]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
