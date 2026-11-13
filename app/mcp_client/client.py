"""MCP client over Streamable HTTP.

One MCP session per chat request (opened lazily on the first tool call and
reused for the rest of that request). The server is stateless, so any server
task can handle any call; no sticky sessions are needed.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from app.agent.types import ToolCallResult, ToolSpec

log = logging.getLogger(__name__)

try:  # newer SDKs
    from mcp.client.streamable_http import streamablehttp_client as _http_client
except ImportError:  # pragma: no cover
    from mcp.client.streamable_http import streamable_http_client as _http_client  # type: ignore


def _parse_result(result: Any) -> ToolCallResult:
    """Turn a CallToolResult into a JSON object for Bedrock's toolResult."""
    is_error = bool(getattr(result, "isError", False))
    texts = [c.text for c in (result.content or []) if getattr(c, "type", None) == "text"]
    if is_error:
        # Server ToolErrors carry user-safe text; keep only that.
        message = " ".join(texts).strip() or "The data lookup failed."
        prefix = "Error executing tool"
        if message.startswith(prefix) and ":" in message:
            message = message.split(":", 1)[1].strip()
        return ToolCallResult(is_error=True, data={"error": message[:500]})
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return ToolCallResult(is_error=False, data=structured)
    joined = "".join(texts)
    try:
        parsed = json.loads(joined)
        if isinstance(parsed, dict):
            return ToolCallResult(is_error=False, data=parsed)
        return ToolCallResult(is_error=False, data={"result": parsed})
    except json.JSONDecodeError:
        return ToolCallResult(is_error=False, data={"text": joined[:4000]})


class _Session:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallResult:
        result = await self._session.call_tool(name, arguments)
        return _parse_result(result)


class McpToolExecutor:
    def __init__(self, url: str, auth_token: str = "", timeout_s: float = 30.0) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        self._timeout = timedelta(seconds=timeout_s)

    @contextlib.asynccontextmanager
    async def _client_session(self) -> AsyncIterator[Any]:
        from mcp import ClientSession

        async with _http_client(self._url, headers=self._headers, timeout=self._timeout) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> list[ToolSpec]:
        async with self._client_session() as session:
            result = await session.list_tools()
        return [
            ToolSpec(name=t.name, description=t.description or "", input_schema=t.inputSchema)
            for t in result.tools
        ]

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[_Session]:
        async with self._client_session() as session:
            yield _Session(session)
