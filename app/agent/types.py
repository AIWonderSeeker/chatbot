from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ToolStatus = Literal["success", "error", "rejected", "timeout"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResult:
    is_error: bool
    data: dict[str, Any]


@dataclass(frozen=True)
class ToolCallRecord:
    """User-visible summary. Never holds arguments or results."""

    name: str
    status: ToolStatus
    duration_ms: int


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    text: str


@dataclass
class AgentResult:
    answer: str
    conversation_id: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class ToolSession(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallResult: ...


class ToolExecutor(Protocol):
    """Implemented by the MCP client (and by fakes in tests)."""

    async def list_tools(self) -> list[ToolSpec]: ...

    def session(self) -> Any:
        """Async context manager yielding a ToolSession for one chat request."""
        ...


class ConverseClient(Protocol):
    async def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]: ...
