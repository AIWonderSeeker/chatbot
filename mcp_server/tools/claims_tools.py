from __future__ import annotations

from typing import Any

from mcp_server.results import normalize_row
from mcp_server.tools.context import ToolContext
from mcp_server.validation import validate_date_range


def get_claim_counts_by_status(
    ctx: ToolContext,
    start_date: object,
    end_date: object,
    *,
    query_tag: str,
) -> dict[str, Any]:
    start, end = validate_date_range(start_date, end_date, ctx.max_date_range_days)
    result = ctx.db.execute(
        ctx.queries["claim_counts_by_status"],
        # SQL LIMIT is one higher than the cap so the client can detect truncation.
        {"start_date": start, "end_date": end, "row_limit": ctx.max_rows + 1},
        row_limit=ctx.max_rows,
        query_tag=query_tag,
    )
    statuses = [normalize_row(r) for r in result.rows]
    return {
        "start_date": start,
        "end_date": end,
        "statuses": statuses,
        "total_claims": sum(int(s.get("claim_count") or 0) for s in statuses),
        "truncated": result.truncated,
        "row_limit": ctx.max_rows,
    }
