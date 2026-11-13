"""The only SQL this system can execute.

Rules (enforced at render time and by tests/test_sql_safety.py):
* One SELECT or WITH statement per template; no semicolons or comments.
* ${...} placeholders are identifiers from SchemaMapping, substituted once at
  startup. They never come from user input.
* %(name)s placeholders are bind parameters, the only path for user values.

>>> UPDATE AFTER RECEIVING THE REAL SCHEMA <<<
Adjust joins/filters here if the real model differs (for example, claim lines
vs. claim headers, or status codes that need a lookup table).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from string import Template

from mcp_server.schema_mapping import SchemaMapping

BIND_RE = re.compile(r"%\((\w+)\)s")
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|call|"
    r"execute|copy|put|remove|use|undrop)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryTemplate:
    query_id: str
    sql: str


@dataclass(frozen=True)
class RenderedQuery:
    query_id: str
    sql: str
    bind_names: frozenset[str]

    def check_params(self, params: dict[str, object]) -> None:
        if set(params) != self.bind_names:
            raise ValueError(f"{self.query_id}: params {sorted(params)} != binds {sorted(self.bind_names)}")


PROVIDER_BY_NPI = QueryTemplate(
    "provider_by_npi",
    """
SELECT ${p_npi} AS npi,
       ${p_name} AS provider_name,
       ${p_specialty} AS specialty,
       ${p_state} AS state,
       ${p_entity_type} AS entity_type
FROM ${providers_table}
WHERE ${p_npi} = %(npi)s
LIMIT %(row_limit)s
""",
)

PROVIDER_CLAIM_SUMMARY = QueryTemplate(
    "provider_claim_summary",
    """
SELECT COUNT(*) AS claim_count,
       COUNT(DISTINCT ${c_member_id}) AS distinct_patients,
       SUM(${c_billed_amount}) AS total_billed_amount,
       SUM(${c_paid_amount}) AS total_paid_amount,
       MIN(${c_claim_date}) AS first_claim_date,
       MAX(${c_claim_date}) AS last_claim_date
FROM ${claims_table}
WHERE ${c_provider_npi} = %(npi)s
  AND ${c_claim_date} BETWEEN %(start_date)s AND %(end_date)s
""",
)

CLAIM_COUNTS_BY_STATUS = QueryTemplate(
    "claim_counts_by_status",
    """
SELECT ${c_status} AS claim_status,
       COUNT(*) AS claim_count,
       SUM(${c_paid_amount}) AS total_paid_amount
FROM ${claims_table}
WHERE ${c_claim_date} BETWEEN %(start_date)s AND %(end_date)s
GROUP BY ${c_status}
ORDER BY claim_count DESC
LIMIT %(row_limit)s
""",
)

ALL_TEMPLATES: tuple[QueryTemplate, ...] = (
    PROVIDER_BY_NPI,
    PROVIDER_CLAIM_SUMMARY,
    CLAIM_COUNTS_BY_STATUS,
)


def assert_template_safe(sql: str) -> None:
    stripped = sql.strip()
    lowered = stripped.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("Template must start with SELECT or WITH")
    if ";" in stripped:
        raise ValueError("Template must not contain semicolons")
    if "--" in stripped or "/*" in stripped:
        raise ValueError("Template must not contain comments")
    if "${" in stripped:
        raise ValueError("Template has an unresolved identifier placeholder")
    if _FORBIDDEN_RE.search(BIND_RE.sub("", stripped)):
        raise ValueError("Template contains a forbidden keyword")


def render(template: QueryTemplate, mapping: SchemaMapping) -> RenderedQuery:
    # substitute() raises KeyError for any placeholder the mapping lacks.
    sql = Template(template.sql).substitute(mapping.template_values()).strip()
    assert_template_safe(sql)
    return RenderedQuery(template.query_id, sql, frozenset(BIND_RE.findall(sql)))


def render_all(mapping: SchemaMapping) -> dict[str, RenderedQuery]:
    return {t.query_id: render(t, mapping) for t in ALL_TEMPLATES}
