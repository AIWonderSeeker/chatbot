import re

import pytest

from mcp_server.queries import (
    ALL_TEMPLATES,
    BIND_RE,
    QueryTemplate,
    assert_template_safe,
    render,
    render_all,
)
from mcp_server.schema_mapping import DEFAULT_MAPPING, SchemaMappingError, load_mapping, validate_mapping


def test_all_templates_render_and_are_select_only():
    rendered = render_all(load_mapping(None))
    assert set(rendered) == {t.query_id for t in ALL_TEMPLATES}
    for q in rendered.values():
        lowered = q.sql.lower()
        assert lowered.startswith(("select", "with"))
        assert ";" not in q.sql
        assert "${" not in q.sql
        assert q.bind_names, f"{q.query_id} has no bind parameters"


def test_user_values_only_via_bind_parameters():
    # After removing bind placeholders, no quote characters or format markers remain.
    for q in render_all(load_mapping(None)).values():
        stripped = BIND_RE.sub("", q.sql)
        assert "'" not in stripped and '"' not in stripped
        assert "%" not in stripped and "{" not in stripped


def test_row_limit_bound_where_multiple_rows_possible():
    rendered = render_all(load_mapping(None))
    assert "row_limit" in rendered["provider_by_npi"].bind_names
    assert "row_limit" in rendered["claim_counts_by_status"].bind_names


@pytest.mark.parametrize(
    "bad",
    [
        "SELECT 1; DROP TABLE x",
        "DELETE FROM claims",
        "SELECT * FROM t -- comment",
        "SELECT * FROM t /* c */",
        "WITH x AS (SELECT 1) INSERT INTO y SELECT * FROM x",
        "SELECT ${unresolved} FROM t",
    ],
)
def test_unsafe_templates_rejected(bad):
    with pytest.raises(ValueError):
        assert_template_safe(bad)


@pytest.mark.parametrize(
    "ident",
    [
        "CLAIMS; DROP TABLE X",
        "CLAIMS--",
        "1CLAIMS",
        "CLAIMS WHERE 1=1",
        '"Claims"',
        "A.B.C.D",
        "",
    ],
)
def test_bad_table_identifiers_rejected(ident):
    raw = {k: dict(v) for k, v in DEFAULT_MAPPING.items()}
    raw["tables"]["claims"] = ident
    with pytest.raises(SchemaMappingError):
        validate_mapping(raw)


def test_qualified_table_names_allowed_but_not_for_columns():
    raw = {k: dict(v) for k, v in DEFAULT_MAPPING.items()}
    raw["tables"]["claims"] = "ANALYTICS.CLAIMS_APP.V_CLAIMS"
    assert validate_mapping(raw).tables["claims"] == "ANALYTICS.CLAIMS_APP.V_CLAIMS"
    raw["claims"]["status"] = "T.CLAIM_STATUS"
    with pytest.raises(SchemaMappingError):
        validate_mapping(raw)


def test_missing_mapping_key_rejected():
    raw = {k: dict(v) for k, v in DEFAULT_MAPPING.items()}
    del raw["claims"]["paid_amount"]
    with pytest.raises(SchemaMappingError):
        validate_mapping(raw)


def test_mapping_file_overrides_merge(tmp_path):
    path = tmp_path / "m.json"
    path.write_text('{"claims": {"claim_date": "SERVICE_DATE"}}')
    mapping = load_mapping(str(path))
    assert mapping.claims["claim_date"] == "SERVICE_DATE"
    assert mapping.claims["status"] == DEFAULT_MAPPING["claims"]["status"]
    assert "SERVICE_DATE" in render_all(mapping)["provider_claim_summary"].sql


def test_check_params_requires_exact_bind_set():
    q = render(ALL_TEMPLATES[0], load_mapping(None))
    q.check_params({"npi": "1234567893", "row_limit": 1})
    with pytest.raises(ValueError):
        q.check_params({"npi": "1234567893"})
    with pytest.raises(ValueError):
        q.check_params({"npi": "x", "row_limit": 1, "extra": 2})


def test_template_with_missing_identifier_fails_render():
    with pytest.raises(KeyError):
        render(QueryTemplate("bad", "SELECT ${nope} FROM ${claims_table}"), load_mapping(None))


def test_no_python_string_formatting_of_sql_in_tools():
    import inspect

    from mcp_server.tools import claims_tools, provider_tools

    for module in (provider_tools, claims_tools):
        source = inspect.getsource(module)
        assert not re.search(r"f\"\"\"\s*select|f\"select|\.format\(", source, re.IGNORECASE)
