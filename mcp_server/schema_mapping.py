"""Configurable mapping from logical names to real Snowflake identifiers.

>>> UPDATE AFTER RECEIVING THE REAL SCHEMA <<<
The defaults below are placeholders. Override them by pointing
SCHEMA_MAPPING_PATH at a JSON file shaped like DEFAULT_MAPPING (see
schema_mapping.example.json). Prefer mapping to purpose-built views that
expose only the columns these tools need.

Identifiers are validated with a strict regex, so a bad mapping fails at
startup instead of producing injectable SQL. Quoted identifiers are not
supported on purpose; create a view with plain names if the source uses them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_IDENT = r"[A-Za-z_][A-Za-z0-9_$]{0,254}"
_TABLE_RE = re.compile(rf"^{_IDENT}(\.{_IDENT}){{0,2}}$")  # optional DB.SCHEMA. prefix
_COLUMN_RE = re.compile(rf"^{_IDENT}$")

DEFAULT_MAPPING: dict[str, dict[str, str]] = {
    "tables": {
        "providers": "PROVIDERS",  # e.g. ANALYTICS.CLAIMS_APP.V_PROVIDERS
        "claims": "CLAIMS",  # e.g. ANALYTICS.CLAIMS_APP.V_CLAIMS
    },
    "providers": {
        "npi": "NPI",
        "name": "PROVIDER_NAME",
        "specialty": "SPECIALTY",
        "state": "STATE",
        "entity_type": "ENTITY_TYPE",
    },
    "claims": {
        "claim_id": "CLAIM_ID",
        "provider_npi": "RENDERING_NPI",  # or BILLING_NPI: confirm with data owners
        "member_id": "MEMBER_ID",  # used only inside COUNT(DISTINCT), never returned
        "claim_date": "SUBMITTED_DATE",  # submission vs. service date: confirm
        "status": "CLAIM_STATUS",
        "billed_amount": "BILLED_AMOUNT",
        "paid_amount": "PAID_AMOUNT",
    },
}


class SchemaMappingError(ValueError):
    pass


@dataclass(frozen=True)
class SchemaMapping:
    tables: dict[str, str]
    providers: dict[str, str]
    claims: dict[str, str]

    def template_values(self) -> dict[str, str]:
        """Flat values for string.Template substitution in queries.py."""
        values = {f"{k}_table": v for k, v in self.tables.items()}
        values.update({f"p_{k}": v for k, v in self.providers.items()})
        values.update({f"c_{k}": v for k, v in self.claims.items()})
        return values


def validate_mapping(raw: dict[str, dict[str, str]]) -> SchemaMapping:
    for section, required in DEFAULT_MAPPING.items():
        got = raw.get(section)
        if not isinstance(got, dict):
            raise SchemaMappingError(f"Mapping section '{section}' is missing")
        missing = set(required) - set(got)
        if missing:
            raise SchemaMappingError(f"Mapping '{section}' missing keys: {sorted(missing)}")
        pattern = _TABLE_RE if section == "tables" else _COLUMN_RE
        for key, ident in got.items():
            if not isinstance(ident, str) or not pattern.fullmatch(ident):
                raise SchemaMappingError(f"Invalid identifier for {section}.{key}: {ident!r}")
    return SchemaMapping(
        tables=dict(raw["tables"]),
        providers=dict(raw["providers"]),
        claims=dict(raw["claims"]),
    )


def load_mapping(path: str | None = None, json_text: str | None = None) -> SchemaMapping:
    """Defaults, overridden by inline JSON (SCHEMA_MAPPING_JSON) or a file (SCHEMA_MAPPING_PATH)."""
    if json_text:
        data = json.loads(json_text)
    elif path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        return validate_mapping(DEFAULT_MAPPING)
    merged = {s: {**DEFAULT_MAPPING[s], **data.get(s, {})} for s in DEFAULT_MAPPING}
    return validate_mapping(merged)
