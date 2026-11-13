"""Normalization and minimization of query results before they leave the server."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(round(value, 2))
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    # Snowflake upper-cases unquoted aliases; SQLite does not. Normalize both.
    return {str(k).lower(): normalize_value(v) for k, v in row.items()}


def suppress_small_count(value: int | None, threshold: int) -> tuple[int | str | None, bool]:
    """Return (display_value, suppressed). Counts in [1, threshold) are hidden."""
    if value is None or threshold <= 0:
        return value, False
    if 0 < value < threshold:
        return f"<{threshold}", True
    return value, False
