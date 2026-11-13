"""Typed agent errors. `user_message` is safe to show; details go to logs only."""

from __future__ import annotations


class AgentError(Exception):
    status_code = 502
    code = "agent_error"
    user_message = "The assistant could not complete that request. Try again."

    def __init__(self, detail: str = "", *, user_message: str | None = None) -> None:
        super().__init__(detail or self.code)
        if user_message:
            self.user_message = user_message


class ModelThrottledError(AgentError):
    status_code = 503
    code = "model_throttled"
    user_message = "The assistant is busy right now. Wait a moment and try again."


class ModelTimeoutError(AgentError):
    status_code = 504
    code = "model_timeout"
    user_message = "The assistant took too long to respond. Try again or ask a narrower question."


class ModelUnavailableError(AgentError):
    status_code = 502
    code = "model_unavailable"
    user_message = "The language model is unavailable. Try again shortly."


class ModelConfigurationError(AgentError):
    status_code = 500
    code = "model_configuration"
    user_message = "The assistant is misconfigured. Contact the administrator."


class ToolsUnavailableError(AgentError):
    status_code = 503
    code = "tools_unavailable"
    user_message = "Data tools are temporarily unavailable. Try again shortly."


class RequestBudgetExceededError(AgentError):
    status_code = 504
    code = "request_timeout"
    user_message = "That request ran out of time. Try a narrower question."
