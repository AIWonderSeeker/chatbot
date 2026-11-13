"""Test doubles: in-process tool executor and scripted Converse client."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.agent.types import ToolCallResult, ToolSpec
from mcp_server.config import ServerSettings
from mcp_server.tools import claims_tools, provider_tools
from mcp_server.tools.registry import build_context
from mcp_server.validation import ToolInputError

_DATE = {"type": "string", "format": "date"}
TOOL_SPECS = [
    ToolSpec(
        "get_provider_by_npi",
        "Look up a provider by NPI.",
        {"type": "object", "properties": {"npi": {"type": "string"}}, "required": ["npi"]},
    ),
    ToolSpec(
        "get_provider_claim_summary",
        "Summarize a provider's claims.",
        {
            "type": "object",
            "properties": {"npi": {"type": "string"}, "start_date": _DATE, "end_date": _DATE},
            "required": ["npi", "start_date", "end_date"],
        },
    ),
    ToolSpec(
        "get_claim_counts_by_status",
        "Count claims by status.",
        {
            "type": "object",
            "properties": {"start_date": _DATE, "end_date": _DATE},
            "required": ["start_date", "end_date"],
        },
    ),
]


class InProcessToolExecutor:
    """Calls the real MCP tool functions (and SQLite mock) without the transport."""

    def __init__(self, extra_specs: list[ToolSpec] | None = None) -> None:
        self.ctx = build_context(ServerSettings(max_rows=50))
        self.specs = TOOL_SPECS + (extra_specs or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sessions_opened = 0
        self.fail_with: Exception | None = None
        self.list_fails = False

    async def list_tools(self) -> list[ToolSpec]:
        if self.list_fails:
            raise ConnectionError("mcp down")
        return list(self.specs)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallResult:
        self.calls.append((name, arguments))
        if self.fail_with:
            raise self.fail_with
        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "get_provider_by_npi": lambda a: provider_tools.get_provider_by_npi(
                self.ctx, a.get("npi"), query_tag="test"
            ),
            "get_provider_claim_summary": lambda a: provider_tools.get_provider_claim_summary(
                self.ctx, a.get("npi"), a.get("start_date"), a.get("end_date"), query_tag="test"
            ),
            "get_claim_counts_by_status": lambda a: claims_tools.get_claim_counts_by_status(
                self.ctx, a.get("start_date"), a.get("end_date"), query_tag="test"
            ),
        }
        if name not in handlers:
            return ToolCallResult(True, {"error": f"Unknown tool {name}"})
        try:
            return ToolCallResult(False, handlers[name](arguments))
        except ToolInputError as exc:
            return ToolCallResult(True, {"error": str(exc)})

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[InProcessToolExecutor]:
        self.sessions_opened += 1
        yield self


def tool_use(name: str, args: Any, tool_id: str = "t1", text: str = "") -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"text": text}] if text else []
    content.append({"toolUse": {"toolUseId": tool_id, "name": name, "input": args}})
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": "tool_use"}


def final(text: str, stop: str = "end_turn") -> dict[str, Any]:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": stop}


class ScriptedConverse:
    """Returns queued responses (or raises queued exceptions) and records requests."""

    def __init__(self, *responses: dict[str, Any] | Exception, delay_s: float = 0.0) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.delay_s = delay_s

    async def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        import asyncio
        import copy

        self.requests.append(
            {
                "system": system,
                "messages": copy.deepcopy(messages),
                "tool_config": tool_config,
                "timeout_s": timeout_s,
            }
        )
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
