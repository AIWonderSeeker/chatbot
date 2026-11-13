"""scripts/generate_fake_npi_data.py produces consistent, loadable fake data."""

import csv
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from mcp_server.validation import validate_npi

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_fake_npi_data.py"
_spec = importlib.util.spec_from_file_location("generate_fake_npi_data", _SCRIPT)
gen = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gen  # dataclasses need the module registered
_spec.loader.exec_module(gen)

START, END = date(2024, 1, 1), date(2025, 12, 31)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("fake")
    providers_path, claims_path, written = gen.write_csvs(
        out, providers=100, claims=10_000, seed=42, start=START, end=END
    )
    return _read(providers_path), _read(claims_path), written


def test_row_counts_and_headers(data):
    providers, claims, written = data
    assert len(providers) == 100
    assert len(claims) == written == 10_000
    assert list(providers[0]) == gen.PROVIDER_COLUMNS
    assert list(claims[0]) == gen.CLAIM_COLUMNS


def test_every_npi_passes_the_servers_validation_and_is_unique(data):
    providers, _, _ = data
    npis = [p["NPI"] for p in providers]
    assert len(set(npis)) == len(npis)
    for npi in npis:
        assert validate_npi(npi) == npi
        assert npi[0] in "12"


def test_demo_npis_present_and_low_volume_one_is_suppressible(data):
    providers, claims, _ = data
    npis = {p["NPI"] for p in providers}
    assert set(gen.SAMPLE_NPIS) <= npis
    low = [c for c in claims if c["RENDERING_NPI"] == gen.LOW_VOLUME_NPI]
    assert len(low) == gen.LOW_VOLUME_CLAIMS
    assert len({c["MEMBER_ID"] for c in low}) < 11  # below the default suppression threshold
    for npi in gen.SAMPLE_NPIS[:-1]:
        assert any(c["RENDERING_NPI"] == npi for c in claims)


def test_claims_reference_known_providers_and_obey_table_checks(data):
    providers, claims, _ = data
    by_npi = {p["NPI"]: p for p in providers}
    ids = set()
    for c in claims:
        assert c["CLAIM_ID"] not in ids
        ids.add(c["CLAIM_ID"])
        assert c["RENDERING_NPI"] in by_npi and c["BILLING_NPI"] in by_npi
        if c["BILLING_NPI"] != c["RENDERING_NPI"]:
            assert by_npi[c["BILLING_NPI"]]["ENTITY_TYPE"] == "Organization"
        service, submitted = date.fromisoformat(c["SERVICE_DATE"]), date.fromisoformat(c["SUBMITTED_DATE"])
        assert START <= service <= submitted <= END
        assert c["CLAIM_STATUS"] in gen.STATUSES
        billed, paid = float(c["BILLED_AMOUNT"]), float(c["PAID_AMOUNT"])
        assert 0 <= paid <= billed
        if c["CLAIM_STATUS"] not in ("PAID", "ADJUSTED"):
            assert paid == 0


def test_provider_fields_fit_the_postgres_columns(data):
    providers, _, _ = data
    for p in providers:
        assert p["ENTITY_TYPE"] in ("Individual", "Organization")
        assert len(p["TAXONOMY_CODE"]) == 10
        assert len(p["STATE"]) == 2 and len(p["ZIP"]) == 5 and p["ZIP"].isdigit()
        date.fromisoformat(p["ENUMERATION_DATE"])


def test_deterministic_for_a_seed(tmp_path):
    a = gen.write_csvs(tmp_path / "a", providers=20, claims=500, seed=7, start=START, end=END)
    b = gen.write_csvs(tmp_path / "b", providers=20, claims=500, seed=7, start=START, end=END)
    c = gen.write_csvs(tmp_path / "c", providers=20, claims=500, seed=8, start=START, end=END)
    assert a[0].read_text() == b[0].read_text() and a[1].read_text() == b[1].read_text()
    assert a[1].read_text() != c[1].read_text()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"providers": 3, "claims": 10},
        {"providers": 10, "claims": -1},
        {"providers": 10, "claims": 10, "start": END, "end": START},
    ],
)
def test_bad_arguments_rejected(tmp_path, kwargs):
    args = {"seed": 1, "start": START, "end": END, **kwargs}
    with pytest.raises(ValueError):
        gen.write_csvs(tmp_path, **args)


def test_check_digit_matches_known_valid_npi():
    assert gen.npi_check_digit("123456789") == "3"
    assert gen.is_valid_npi("1234567893") and not gen.is_valid_npi("1234567890")
