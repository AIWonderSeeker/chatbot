from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    sub: str
    email: str | None = None
