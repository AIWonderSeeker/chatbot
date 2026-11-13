"""Bedrock Converse tool-use loop.

process() = load history -> discover tools -> converse -> (toolUse -> MCP ->
toolResult)* -> final text -> persist the text turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.errors import AgentError, ModelTimeoutError, RequestBudgetExceededError
from app.agent.prompts import build_system_prompt
from app.agent.tool_registry import ToolRegistry
from app.agent.types import (
    AgentResult,
    ConverseClient,
    ToolCallRecord,
    ToolExecutor,
    ToolSession,
    ToolStatus,
    Turn,
)
from app.storage.base import ConversationStore, LoadStatus

log = logging.getLogger(__name__)
_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

MAX_ROUNDS_MESSAGE = (
    "I couldn't finish answering within the allowed number of data lookups. "
    "Try breaking the question into smaller parts."
)
FILTERED_MESSAGE = "I can't help with that request."
EMPTY_MESSAGE = "I couldn't produce an answer from the available data. Try rephrasing the question."


@dataclass(frozen=True)
class AgentSettings:
    max_tool_rounds: int = 5
    max_parallel_tools: int = 4
    tool_timeout_s: float = 20.0
    model_timeout_s: float = 45.0
    max_history_turns: int = 10
    min_call_budget_s: float = 1.0


class BedrockAgent:
    def __init__(
        self,
        *,
        model: ConverseClient,
        registry: ToolRegistry,
        executor: ToolExecutor,
        store: ConversationStore,
        settings: AgentSettings,
        prompt_builder: Callable[[], str] = build_system_prompt,
    ) -> None:
        self._model = model
        self._registry = registry
        self._executor = executor
        self._store = store
        self._settings = settings
        self._prompt_builder = prompt_builder

    async def process(
        self,
        message: str,
        conversation_id: str | None,
        user_id: str,
        *,
        budget_s: float,
    ) -> AgentResult:
        deadline = time.monotonic() + budget_s
        cid, history = await self._resolve_conversation(conversation_id, user_id)
        tool_config = await self._registry.bedrock_tool_config()

        messages: list[dict[str, Any]] = [{"role": t.role, "content": [{"text": t.text}]} for t in history]
        messages.append({"role": "user", "content": [{"text": message}]})

        records: list[ToolCallRecord] = []
        async with contextlib.AsyncExitStack() as stack:
            answer = await self._run_loop(messages, tool_config, deadline, records, stack)

        try:
            await self._store.append(
                cid,
                user_id,
                [Turn("user", message), Turn("assistant", answer)],
                max_turns=self._settings.max_history_turns,
            )
        except Exception:  # noqa: BLE001 - the answer is still valid; history is best effort
            log.exception("failed to persist conversation turn")
        return AgentResult(answer=answer, conversation_id=cid, tool_calls=records)

    async def _resolve_conversation(
        self, conversation_id: str | None, user_id: str
    ) -> tuple[str, list[Turn]]:
        if not conversation_id:
            return str(uuid.uuid4()), []
        status, turns = await self._store.load(conversation_id, user_id)
        if status is LoadStatus.FORBIDDEN:
            log.warning("conversation owned by another user; starting new conversation")
            return str(uuid.uuid4()), []
        return conversation_id, turns[-2 * self._settings.max_history_turns :]

    def _remaining(self, deadline: float) -> float:
        return deadline - time.monotonic()

    async def _converse(
        self,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None,
        deadline: float,
    ) -> dict[str, Any]:
        remaining = self._remaining(deadline)
        if remaining < self._settings.min_call_budget_s:
            raise RequestBudgetExceededError("budget exhausted before model call")
        timeout = min(self._settings.model_timeout_s, remaining)
        try:
            return await asyncio.wait_for(
                self._model.converse(
                    system=self._prompt_builder(),
                    messages=messages,
                    tool_config=tool_config,
                    timeout_s=timeout,
                ),
                timeout + 0.5,
            )
        except TimeoutError as exc:
            if self._remaining(deadline) <= 0.5:
                raise RequestBudgetExceededError("budget exhausted during model call") from exc
            raise ModelTimeoutError("model call timed out") from exc

    async def _run_loop(
        self,
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None,
        deadline: float,
        records: list[ToolCallRecord],
        stack: contextlib.AsyncExitStack,
    ) -> str:
        session: ToolSession | None = None
        semaphore = asyncio.Semaphore(self._settings.max_parallel_tools)

        for round_no in range(self._settings.max_tool_rounds + 1):
            response = await self._converse(messages, tool_config, deadline)
            output = (response.get("output") or {}).get("message")
            if not isinstance(output, dict):
                raise AgentError("model response missing output message")
            messages.append(output)
            stop = response.get("stopReason")
            content = output.get("content") or []
            text = "".join(b["text"] for b in content if isinstance(b, dict) and "text" in b).strip()

            if stop == "tool_use":
                tool_uses = [b["toolUse"] for b in content if isinstance(b, dict) and "toolUse" in b]
                if not tool_uses:
                    return text or EMPTY_MESSAGE
                if round_no >= self._settings.max_tool_rounds:
                    log.warning("max tool rounds reached", extra={"rounds": round_no})
                    return MAX_ROUNDS_MESSAGE
                if session is None:
                    session = await stack.enter_async_context(self._executor.session())
                active = session
                assert active is not None  # opened just above
                results = await asyncio.gather(
                    *(self._run_tool(tu, active, deadline, semaphore, records) for tu in tool_uses)
                )
                messages.append({"role": "user", "content": list(results)})
                continue

            if stop in ("guardrail_intervened", "content_filtered"):
                return FILTERED_MESSAGE
            if stop == "max_tokens":
                return (text + "\n\n(Answer cut short. Ask for less detail.)").strip()
            return text or EMPTY_MESSAGE

        return MAX_ROUNDS_MESSAGE

    async def _run_tool(
        self,
        tool_use: dict[str, Any],
        session: ToolSession,
        deadline: float,
        semaphore: asyncio.Semaphore,
        records: list[ToolCallRecord],
    ) -> dict[str, Any]:
        name = tool_use.get("name")
        tool_use_id = str(tool_use.get("toolUseId", ""))
        arguments = tool_use.get("input")
        started = time.monotonic()
        status: ToolStatus
        payload: dict[str, Any]

        if not isinstance(name, str) or not self._registry.is_allowed(name):
            status, payload = "rejected", {"error": "That tool is not available."}
        elif not isinstance(arguments, dict):
            status, payload = "error", {"error": "Tool arguments must be a JSON object."}
        else:
            async with semaphore:
                timeout = min(self._settings.tool_timeout_s, self._remaining(deadline))
                if timeout < self._settings.min_call_budget_s:
                    status, payload = "timeout", {"error": "No time left for this data lookup."}
                else:
                    try:
                        result = await asyncio.wait_for(session.call_tool(name, arguments), timeout)
                        status = "error" if result.is_error else "success"
                        payload = result.data
                    except TimeoutError:
                        status, payload = "timeout", {"error": "The data lookup timed out."}
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        log.exception("tool call failed", extra={"tool": name})
                        status, payload = "error", {"error": "The data lookup failed."}

        duration_ms = round((time.monotonic() - started) * 1000)
        safe_name = name if isinstance(name, str) and _SAFE_TOOL_NAME.fullmatch(name) else "unknown"
        records.append(ToolCallRecord(name=safe_name, status=status, duration_ms=duration_ms))
        log.info("tool call", extra={"tool": safe_name, "status": status, "duration_ms": duration_ms})
        return {
            "toolResult": {
                "toolUseId": tool_use_id,
                "content": [{"json": payload}],
                "status": "success" if status == "success" else "error",
            }
        }
