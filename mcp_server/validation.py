"""Typed input validation for tool arguments.

Every tool argument passes through one of these functions before it gets
anywhere near a query. Error messages are safe to show to the model and user.
"""

from __future__ import annotations

import re
from datetime import date

_NPI_RE = re.compile(r"^\d{10}$")


class ToolInputError(ValueError):
    """Raised for invalid tool arguments. The message is user-safe."""


def _npi_check_digit_valid(npi: str) -> bool:
    # NPI uses the Luhn algorithm over the full number prefixed with 80840.
    digits = "80840" + npi
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def validate_npi(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ToolInputError("NPI must be a 10-digit string.")
    npi = value.strip()
    if not _NPI_RE.fullmatch(npi):
        raise ToolInputError("NPI must be exactly 10 digits.")
    if not _npi_check_digit_valid(npi):
        raise ToolInputError("That NPI has an invalid check digit.")
    return npi


def parse_iso_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise ToolInputError(f"{field_name} must be a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolInputError(f"{field_name} must be a date in YYYY-MM-DD format.") from exc


def validate_date_range(start: object, end: object, max_days: int) -> tuple[str, str]:
    start_d = parse_iso_date(start, "start_date")
    end_d = parse_iso_date(end, "end_date")
    if start_d > end_d:
        raise ToolInputError("start_date must be on or before end_date.")
    if (end_d - start_d).days > max_days:
        raise ToolInputError(f"Date range cannot exceed {max_days} days.")
    if start_d.year < 1990 or end_d.year > 2100:
        raise ToolInputError("Dates must be between 1990 and 2100.")
    return start_d.isoformat(), end_d.isoformat()
