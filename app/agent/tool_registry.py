"""Discovers tools from the MCP server and intersects them with the allowlist."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.agent.errors import ToolsUnavailableError
from app.agent.types import ToolExecutor, ToolSpec

log = logging.getLogger(__name__)
_BEDROCK_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ToolRegistry:
    def __init__(
        self,
        executor: ToolExecutor,
        allowlist: frozenset[str],
        cache_ttl_s: float = 300.0,
        discovery_timeout_s: float = 5.0,
    ) -> None:
        self._executor = executor
        self._allowlist = allowlist
        self._ttl = cache_ttl_s
        self._timeout = discovery_timeout_s
        self._specs: dict[str, ToolSpec] = {}
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._loaded_at is not None

    def is_allowed(self, name: str) -> bool:
        return name in self._allowlist and name in self._specs

    async def refresh(self) -> None:
        try:
            discovered = await asyncio.wait_for(self._executor.list_tools(), self._timeout)
        except Exception as exc:  # noqa: BLE001
            raise ToolsUnavailableError(f"tool discovery failed: {type(exc).__name__}") from exc
        specs: dict[str, ToolSpec] = {}
        for spec in discovered:
            if spec.name not in self._allowlist:
                log.info("ignoring tool not in allowlist", extra={"tool": spec.name})
                continue
            if not _BEDROCK_TOOL_NAME.fullmatch(spec.name):
                log.warning("tool name not valid for Bedrock", extra={"tool": spec.name})
                continue
            specs[spec.name] = spec
        missing = self._allowlist - set(specs)
        if missing:
            log.warning("allowlisted tools not offered by server", extra={"tools": sorted(missing)})
        self._specs = specs
        self._loaded_at = time.monotonic()

    async def ensure_loaded(self) -> None:
        if self._loaded_at is not None and time.monotonic() - self._loaded_at < self._ttl:
            return
        async with self._lock:
            if self._loaded_at is not None and time.monotonic() - self._loaded_at < self._ttl:
                return
            try:
                await self.refresh()
            except ToolsUnavailableError:
                if self._specs:  # serve the stale list rather than failing outright
                    log.warning("tool refresh failed; using cached list")
                    self._loaded_at = time.monotonic()
                    return
                raise

    async def bedrock_tool_config(self) -> dict[str, Any] | None:
        await self.ensure_loaded()
        if not self._specs:
            return None
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": s.name,
                        "description": s.description[:1000] or s.name,
                        "inputSchema": {"json": s.input_schema},
                    }
                }
                for s in self._specs.values()
            ]
        }
