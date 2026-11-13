"""Structured JSON logging with request IDs and key-based redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Keys whose values must never reach logs (PHI, prompts, results, secrets).
REDACT_KEYS = frozenset(
    {
        "message",
        "answer",
        "text",
        "input",
        "arguments",
        "rows",
        "result",
        "results",
        "payload",
        "authorization",
        "token",
        "password",
        "private_key",
        "npi",
        "prompt",
    }
)
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in REDACT_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = _redact(value, key)
        if record.exc_info:
            # Exception type only: messages can contain bound values or payloads.
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    for noisy in ("botocore", "boto3", "urllib3", "httpx", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
