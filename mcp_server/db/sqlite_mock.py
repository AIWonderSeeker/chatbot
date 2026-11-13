"""In-memory SQLite stand-in for Snowflake.

Runs the *same* SQL templates as production (bind syntax is translated from
pyformat to SQLite's named style), seeded with deterministic synthetic data.
No real PHI: names, members, and amounts are generated.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from datetime import date, timedelta
from typing import Any

from mcp_server.db.base import QueryExecutionError, QueryResult
from mcp_server.queries import BIND_RE, RenderedQuery
from mcp_server.schema_mapping import SchemaMapping, load_mapping

# Valid NPIs (Luhn-checked) used by the UI examples, README, and evals.
SAMPLE_NPIS: tuple[str, ...] = ("1234567893", "1003000019", "1003000027", "1003000035")

_SPECIALTIES = [
    "Family Medicine",
    "Cardiology",
    "Orthopedic Surgery",
    "Dermatology",
    "Pediatrics",
    "Psychiatry",
    "Radiology",
    "Physical Therapy",
]
_STATES = ["VA", "MD", "DC", "NC", "PA", "NY", "OH", "GA"]
_STATUSES = ["PAID", "DENIED", "PENDING", "ADJUSTED", "REJECTED"]
_STATUS_WEIGHTS = [62, 14, 12, 8, 4]
_FIRST = ["Avery", "Jordan", "Morgan", "Riley", "Casey", "Quinn", "Harper", "Rowan"]
_LAST = ["Okafor", "Lindqvist", "Patel", "Moreau", "Nakamura", "Alvarez", "Brennan", "Hughes"]


def _npi_with_check_digit(base9: str) -> str:
    digits = "80840" + base9
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return base9 + str((10 - total % 10) % 10)


class SqliteMockClient:
    def __init__(self, mapping: SchemaMapping | None = None, seed: int = 42) -> None:
        # The mock always uses unqualified default names: SQLite has no DB.SCHEMA.
        self._mapping = mapping or load_mapping(None)
        if any("." in t for t in self._mapping.tables.values()):
            self._mapping = load_mapping(None)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._seed(seed)

    @property
    def mapping(self) -> SchemaMapping:
        return self._mapping

    def _seed(self, seed: int) -> None:
        m = self._mapping
        p, c, t = m.providers, m.claims, m.tables
        rng = random.Random(seed)  # noqa: S311 - deterministic fake data, not security
        cur = self._conn.cursor()
        cur.execute(
            f"CREATE TABLE {t['providers']} ({p['npi']} TEXT PRIMARY KEY, {p['name']} TEXT, "
            f"{p['specialty']} TEXT, {p['state']} TEXT, {p['entity_type']} TEXT)"
        )
        cur.execute(
            f"CREATE TABLE {t['claims']} ({c['claim_id']} TEXT PRIMARY KEY, "
            f"{c['provider_npi']} TEXT, {c['member_id']} TEXT, {c['claim_date']} TEXT, "
            f"{c['status']} TEXT, {c['billed_amount']} REAL, {c['paid_amount']} REAL)"
        )

        npis = list(SAMPLE_NPIS) + [_npi_with_check_digit(f"1{n:08d}") for n in range(4100, 4120)]
        providers = []
        for i, npi in enumerate(npis):
            org = i % 6 == 5
            name = (
                f"{rng.choice(_LAST)} {rng.choice(['Clinic', 'Medical Group', 'Health'])}"
                if org
                else f"Dr. {rng.choice(_FIRST)} {rng.choice(_LAST)}"
            )
            providers.append(
                (
                    npi,
                    name,
                    rng.choice(_SPECIALTIES),
                    rng.choice(_STATES),
                    "Organization" if org else "Individual",
                )
            )
        cur.executemany(f"INSERT INTO {t['providers']} VALUES (?,?,?,?,?)", providers)

        claims = []
        start = date(2024, 1, 1)
        claim_no = 0
        for idx, npi in enumerate(npis):
            # The last sample NPI gets very few claims to demonstrate suppression.
            volume = 6 if npi == SAMPLE_NPIS[-1] else rng.randint(60, 260)
            members = [f"M{idx:03d}{k:05d}" for k in range(max(3, volume // 3))]
            for _ in range(volume):
                claim_no += 1
                day = start + timedelta(days=rng.randint(0, 730))
                status = rng.choices(_STATUSES, _STATUS_WEIGHTS)[0]
                billed = round(rng.uniform(80, 2400), 2)
                paid = round(billed * rng.uniform(0.35, 0.9), 2) if status in ("PAID", "ADJUSTED") else 0.0
                claims.append(
                    (f"C{claim_no:08d}", npi, rng.choice(members), day.isoformat(), status, billed, paid)
                )
        cur.executemany(f"INSERT INTO {t['claims']} VALUES (?,?,?,?,?,?,?)", claims)
        self._conn.commit()

    def execute(
        self,
        query: RenderedQuery,
        params: dict[str, Any],
        *,
        row_limit: int,
        query_tag: str,
    ) -> QueryResult:
        query.check_params(params)
        sql = BIND_RE.sub(r":\1", query.sql)
        try:
            with self._lock:
                cur = self._conn.execute(sql, params)
                fetched = cur.fetchmany(row_limit + 1)
        except sqlite3.Error as exc:
            raise QueryExecutionError(f"{query.query_id} failed: {exc}") from exc
        rows = [dict(r) for r in fetched[:row_limit]]
        return QueryResult(rows=rows, truncated=len(fetched) > row_limit)

    def close(self) -> None:
        self._conn.close()
