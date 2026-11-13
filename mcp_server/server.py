"""Long-running MCP server exposing read-only Snowflake tools.

Transport: Streamable HTTP in stateless mode with JSON responses, so any task
behind Service Connect can serve any request. Run with:

    uvicorn mcp_server.server:app --host 0.0.0.0 --port 8001

The ASGI app is the FastMCP Streamable HTTP app wrapped with a bearer-token
check (every path except /health) and a /health route for ECS.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import sys
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_server.config import ServerSettings
from mcp_server.db.base import QueryExecutionError
from mcp_server.tools import claims_tools, provider_tools
from mcp_server.tools.registry import build_context
from mcp_server.validation import ToolInputError

settings = ServerSettings.from_env()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        return json.dumps(payload, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=settings.log_level, handlers=[_handler], force=True)
log = logging.getLogger("mcp_server")

ctx = build_context(settings)
_db_pool = ThreadPoolExecutor(max_workers=settings.db_thread_workers, thread_name_prefix="db")

# host="0.0.0.0" also stops newer SDKs from auto-enabling localhost-only
# DNS-rebinding protection, which would reject Host: mcp:8001 from other tasks.
mcp = FastMCP(
    "snowflake-data-tools",
    instructions="Read-only, parameterized lookups over healthcare provider and claims data.",
    host=settings.host,
    port=settings.port,
    stateless_http=True,
    json_response=True,
)


async def _run(tool: str, fn: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """Run a blocking tool in the DB pool; convert errors into safe ToolErrors."""
    query_tag = json.dumps({"app": "nl-data-assistant", "tool": tool, "id": str(uuid.uuid4())})
    started = time.perf_counter()
    status = "success"
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_db_pool, fn, query_tag)
    except ToolInputError as exc:
        status = "invalid_argument"
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - details stay in logs, never in the response
        status = "error"
        fields: dict[str, Any] = {"tool": tool, "error_type": type(exc).__name__}
        if isinstance(exc, QueryExecutionError):
            # These messages are written to be safe (query id, error class, sqlstate; no values).
            fields["error"] = str(exc)[:300]
        log.error("tool failed", extra={"extra_fields": fields})
        raise ToolError("The data query failed. Try again or narrow the request.") from None
    finally:
        log.info(
            "tool call",
            extra={
                "extra_fields": {
                    "tool": tool,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "query_tag": query_tag,
                }
            },
        )


@mcp.tool(
    description=(
        "Look up a healthcare provider by 10-digit NPI. Returns name, specialty, "
        "state, and entity type, or found=false."
    )
)
async def get_provider_by_npi(npi: str) -> dict[str, Any]:
    return await _run(
        "get_provider_by_npi",
        lambda tag: provider_tools.get_provider_by_npi(ctx, npi, query_tag=tag),
    )


@mcp.tool(
    description=(
        "Summarize claims for one provider (10-digit NPI) between start_date and "
        "end_date inclusive (YYYY-MM-DD): claim count, distinct patients (small "
        "counts suppressed), billed and paid totals, first/last claim date."
    )
)
async def get_provider_claim_summary(npi: str, start_date: str, end_date: str) -> dict[str, Any]:
    return await _run(
        "get_provider_claim_summary",
        lambda tag: provider_tools.get_provider_claim_summary(ctx, npi, start_date, end_date, query_tag=tag),
    )


@mcp.tool(
    description=(
        "Count all claims by claim status between start_date and end_date "
        "inclusive (YYYY-MM-DD), with paid totals per status."
    )
)
async def get_claim_counts_by_status(start_date: str, end_date: str) -> dict[str, Any]:
    return await _run(
        "get_claim_counts_by_status",
        lambda tag: claims_tools.get_claim_counts_by_status(ctx, start_date, end_date, query_tag=tag),
    )


class BearerAuthMiddleware:
    """Pure ASGI middleware (safe for streaming responses)."""

    def __init__(self, app: ASGIApp, token: str, open_paths: frozenset[str]) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode() if token else b""
        self.open_paths = open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.expected or scope.get("path") in self.open_paths:
            await self.app(scope, receive, send)
            return
        provided = dict(scope.get("headers") or []).get(b"authorization", b"")
        if not hmac.compare_digest(provided, self.expected):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "db_backend": settings.backend})


_starlette_app = mcp.streamable_http_app()  # serves POST /mcp; lifespan runs the session manager
_starlette_app.add_route("/health", health, methods=["GET"])

if not settings.auth_token:
    log.warning("MCP_AUTH_TOKEN is empty: tool endpoint is unauthenticated (local only)")

app = BearerAuthMiddleware(_starlette_app, settings.auth_token, frozenset({"/health"}))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
