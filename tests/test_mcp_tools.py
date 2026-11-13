import pytest

from mcp_server.config import ServerSettings
from mcp_server.db.sqlite_mock import SAMPLE_NPIS
from mcp_server.results import suppress_small_count
from mcp_server.tools import claims_tools, provider_tools
from mcp_server.tools.registry import build_context
from mcp_server.validation import ToolInputError, validate_date_range, validate_npi


@pytest.fixture(scope="module")
def ctx():
    return build_context(ServerSettings(max_rows=3, small_cell_threshold=11))


@pytest.mark.parametrize("npi", ["1234567893", 1234567893, " 1003000019 "])
def test_valid_npis(npi):
    assert validate_npi(npi) == str(npi).strip()


@pytest.mark.parametrize("npi", ["12345", "1234567890", "abcdefghij", None, True, "1234567893'--"])
def test_invalid_npis(npi):
    with pytest.raises(ToolInputError):
        validate_npi(npi)


def test_date_range_validation():
    assert validate_date_range("2025-01-01", "2025-12-31", 731) == ("2025-01-01", "2025-12-31")
    for start, end in [
        ("2025-12-31", "2025-01-01"),
        ("2020-01-01", "2025-01-01"),
        ("01/02/2025", "2025-03-01"),
        ("2025-02-30", "2025-03-01"),
        (None, "2025-01-01"),
    ]:
        with pytest.raises(ToolInputError):
            validate_date_range(start, end, 731)


def test_provider_found(ctx):
    result = provider_tools.get_provider_by_npi(ctx, SAMPLE_NPIS[0], query_tag="t")
    assert result["found"] is True
    assert result["provider"]["npi"] == SAMPLE_NPIS[0]
    assert set(result["provider"]) == {"npi", "provider_name", "specialty", "state", "entity_type"}


def test_provider_not_found(ctx):
    result = provider_tools.get_provider_by_npi(ctx, "1003000043", query_tag="t")
    assert result == {"found": False, "npi": "1003000043"}


def test_claim_summary_has_no_member_identifiers(ctx):
    result = provider_tools.get_provider_claim_summary(
        ctx, SAMPLE_NPIS[0], "2024-01-01", "2025-12-31", query_tag="t"
    )
    assert result["claim_count"] > 0
    assert "member_id" not in result
    assert not any(isinstance(v, str) and v.startswith("M0") for v in result.values())


def test_small_cell_suppression(ctx):
    result = provider_tools.get_provider_claim_summary(
        ctx, SAMPLE_NPIS[-1], "2024-01-01", "2025-12-31", query_tag="t"
    )
    assert result["patient_count_suppressed"] is True
    assert result["distinct_patients"] == "<11"


def test_suppression_helper():
    assert suppress_small_count(5, 11) == ("<11", True)
    assert suppress_small_count(0, 11) == (0, False)
    assert suppress_small_count(11, 11) == (11, False)
    assert suppress_small_count(5, 0) == (5, False)


def test_empty_range_returns_zeroes(ctx):
    result = provider_tools.get_provider_claim_summary(
        ctx, SAMPLE_NPIS[0], "2030-01-01", "2030-12-31", query_tag="t"
    )
    assert result["claim_count"] == 0 and result["distinct_patients"] == 0


def test_status_counts_row_limit_and_truncation(ctx):
    result = claims_tools.get_claim_counts_by_status(ctx, "2024-01-01", "2025-12-31", query_tag="t")
    assert len(result["statuses"]) == 3  # max_rows=3, mock has 5 statuses
    assert result["truncated"] is True
    counts = [s["claim_count"] for s in result["statuses"]]
    assert counts == sorted(counts, reverse=True)


def test_injection_strings_never_reach_sql(ctx):
    with pytest.raises(ToolInputError):
        claims_tools.get_claim_counts_by_status(ctx, "2025-01-01' OR '1'='1", "2025-12-31", query_tag="t")
