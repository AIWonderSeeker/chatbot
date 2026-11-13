"""Provider lookups. Provider directory data (NPPES-style) is not member PHI."""

from __future__ import annotations

from typing import Any

from mcp_server.results import normalize_row, suppress_small_count
from mcp_server.tools.context import ToolContext
from mcp_server.validation import validate_date_range, validate_npi


def get_provider_by_npi(ctx: ToolContext, npi: object, *, query_tag: str) -> dict[str, Any]:
    npi_v = validate_npi(npi)
    result = ctx.db.execute(
        ctx.queries["provider_by_npi"],
        {"npi": npi_v, "row_limit": 1},
        row_limit=1,
        query_tag=query_tag,
    )
    if not result.rows:
        return {"found": False, "npi": npi_v}
    return {"found": True, "provider": normalize_row(result.rows[0])}


def get_provider_claim_summary(
    ctx: ToolContext,
    npi: object,
    start_date: object,
    end_date: object,
    *,
    query_tag: str,
) -> dict[str, Any]:
    npi_v = validate_npi(npi)
    start, end = validate_date_range(start_date, end_date, ctx.max_date_range_days)
    result = ctx.db.execute(
        ctx.queries["provider_claim_summary"],
        {"npi": npi_v, "start_date": start, "end_date": end},
        row_limit=1,
        query_tag=query_tag,
    )
    row = normalize_row(result.rows[0]) if result.rows else {}
    claim_count = int(row.get("claim_count") or 0)
    patients, suppressed = suppress_small_count(row.get("distinct_patients"), ctx.small_cell_threshold)
    return {
        "npi": npi_v,
        "start_date": start,
        "end_date": end,
        "claim_count": claim_count,
        "distinct_patients": patients if claim_count else 0,
        "patient_count_suppressed": suppressed,
        "total_billed_amount": row.get("total_billed_amount") or 0.0,
        "total_paid_amount": row.get("total_paid_amount") or 0.0,
        "first_claim_date": row.get("first_claim_date"),
        "last_claim_date": row.get("last_claim_date"),
    }
