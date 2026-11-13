"""Generate fake provider (NPI) and claims data as CSV files for loading into a database.

Everything is synthetic: names, members, and amounts are invented. NPIs have a valid
check digit (Luhn over the 80840 prefix, same rule the MCP server enforces) but are
otherwise random, so some may coincidentally match real providers; do not treat any
row as describing a real person or organization.

Columns match the default schema mapping in mcp_server/schema_mapping.py, plus a few
extra descriptive columns. Output is deterministic for a given --seed.

    python scripts/generate_fake_npi_data.py                       # 100 providers, 10,000 claims
    python scripts/generate_fake_npi_data.py --providers 1000 --claims 100000 --out-dir data/fake

Standard library only; runs on Python 3.9+ (e.g. AWS CloudShell's python3) as well as 3.12.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# NPIs referenced by the UI examples, README, and evals. Always included so the
# demo questions return data. The last one gets very few claims to exercise
# small-cell suppression.
SAMPLE_NPIS: tuple[str, ...] = ("1234567893", "1003000019", "1003000027", "1003000035")
LOW_VOLUME_NPI = SAMPLE_NPIS[-1]
LOW_VOLUME_CLAIMS = 6

PROVIDER_COLUMNS = [
    "NPI",
    "PROVIDER_NAME",
    "ENTITY_TYPE",
    "SPECIALTY",
    "TAXONOMY_CODE",
    "CITY",
    "STATE",
    "ZIP",
    "ENUMERATION_DATE",
]
CLAIM_COLUMNS = [
    "CLAIM_ID",
    "RENDERING_NPI",
    "BILLING_NPI",
    "MEMBER_ID",
    "SERVICE_DATE",
    "SUBMITTED_DATE",
    "CLAIM_STATUS",
    "BILLED_AMOUNT",
    "PAID_AMOUNT",
]

# (specialty, NUCC taxonomy code, typical billed range in dollars)
INDIVIDUAL_SPECIALTIES: list[tuple[str, str, tuple[int, int]]] = [
    ("Family Medicine", "207Q00000X", (90, 450)),
    ("Internal Medicine", "207R00000X", (100, 500)),
    ("Pediatrics", "208000000X", (90, 400)),
    ("Cardiology", "207RC0000X", (250, 3500)),
    ("Dermatology", "207N00000X", (120, 900)),
    ("Orthopedic Surgery", "207X00000X", (400, 6000)),
    ("Psychiatry", "2084P0800X", (150, 600)),
    ("Diagnostic Radiology", "2085R0202X", (200, 2500)),
    ("Obstetrics & Gynecology", "207V00000X", (150, 2800)),
    ("Physical Therapy", "225100000X", (80, 300)),
    ("Nurse Practitioner", "363L00000X", (80, 350)),
]
ORGANIZATION_SPECIALTIES: list[tuple[str, str, tuple[int, int]]] = [
    ("Multi-Specialty Clinic", "261QM1300X", (120, 1500)),
    ("Urgent Care", "261QU0200X", (120, 700)),
    ("Ambulatory Surgical Center", "261QA1903X", (900, 9000)),
    ("Clinical Laboratory", "291U00000X", (25, 400)),
]
ORG_SUFFIXES = ["Medical Group", "Health Partners", "Clinic", "Care Center", "Physicians"]

LOCATIONS: list[tuple[str, str, str]] = [
    ("Arlington", "VA", "222"),
    ("Richmond", "VA", "232"),
    ("Baltimore", "MD", "212"),
    ("Bethesda", "MD", "208"),
    ("Washington", "DC", "200"),
    ("Raleigh", "NC", "276"),
    ("Charlotte", "NC", "282"),
    ("Philadelphia", "PA", "191"),
    ("Pittsburgh", "PA", "152"),
    ("Albany", "NY", "122"),
    ("Columbus", "OH", "432"),
    ("Atlanta", "GA", "303"),
]

STATUSES = ["PAID", "DENIED", "PENDING", "ADJUSTED", "REJECTED"]
STATUS_WEIGHTS = [62, 14, 12, 8, 4]

FIRST_NAMES = [
    "Avery", "Jordan", "Morgan", "Riley", "Casey", "Quinn", "Harper", "Rowan", "Elena", "Marcus",
    "Priya", "Tomas", "Aisha", "Daniel", "Mei", "Samuel", "Leila", "Owen", "Nadia", "Victor",
]  # fmt: skip
LAST_NAMES = [
    "Okafor", "Lindqvist", "Patel", "Moreau", "Nakamura", "Alvarez", "Brennan", "Hughes", "Kowalski",
    "Haddad", "Fischer", "Osei", "Romano", "Sato", "Delgado", "Novak", "Grant", "Iyer", "Byrne", "Costa",
]  # fmt: skip


def npi_check_digit(base9: str) -> str:
    """Check digit for a 9-digit NPI base (Luhn over '80840' + base)."""
    digits = "80840" + base9
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # rightmost digit of the base is doubled (check digit not yet appended)
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def is_valid_npi(npi: str) -> bool:
    return len(npi) == 10 and npi.isdigit() and npi_check_digit(npi[:9]) == npi[9]


@dataclass(frozen=True)
class Provider:
    npi: str
    name: str
    entity_type: str
    specialty: str
    taxonomy_code: str
    city: str
    state: str
    zip_code: str
    enumeration_date: date
    billed_range: tuple[int, int]

    def row(self) -> list[str]:
        return [
            self.npi,
            self.name,
            self.entity_type,
            self.specialty,
            self.taxonomy_code,
            self.city,
            self.state,
            self.zip_code,
            self.enumeration_date.isoformat(),
        ]


def _unique_npis(rng: random.Random, count: int) -> list[str]:
    npis = list(SAMPLE_NPIS[:count])
    seen = set(npis)
    while len(npis) < count:
        base = rng.choice("12") + f"{rng.randrange(10**8):08d}"
        npi = base + npi_check_digit(base)
        if npi not in seen:
            seen.add(npi)
            npis.append(npi)
    return npis


def generate_providers(rng: random.Random, count: int) -> list[Provider]:
    providers = []
    for npi in _unique_npis(rng, count):
        is_org = rng.random() < 0.2  # roughly 1 in 5 is an organization
        if npi in SAMPLE_NPIS:
            is_org = False  # keep demo NPIs as individual clinicians
        specialty, taxonomy, billed = rng.choice(
            ORGANIZATION_SPECIALTIES if is_org else INDIVIDUAL_SPECIALTIES
        )
        city, state, zip3 = rng.choice(LOCATIONS)
        if is_org:
            name = f"{rng.choice(LAST_NAMES)} {rng.choice(ORG_SUFFIXES)}"
        else:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        providers.append(
            Provider(
                npi=npi,
                name=name,
                entity_type="Organization" if is_org else "Individual",
                specialty=specialty,
                taxonomy_code=taxonomy,
                city=city,
                state=state,
                zip_code=f"{zip3}{rng.randrange(100):02d}",
                enumeration_date=date(2006, 1, 1) + timedelta(days=rng.randrange(6500)),
                billed_range=billed,
            )
        )
    return providers


def _claim_volumes(rng: random.Random, providers: list[Provider], total: int) -> list[int]:
    """Split `total` claims across providers: most near the median, a few busy ones."""
    low = [p.npi == LOW_VOLUME_NPI for p in providers]
    reserved = LOW_VOLUME_CLAIMS * sum(low)
    weights = [0.0 if is_low else rng.lognormvariate(0, 0.8) for is_low in low]
    remaining = max(total - reserved, 0)
    scale = sum(weights) or 1.0
    volumes = [int(remaining * w / scale) for w in weights]
    # Hand out the rounding remainder one claim at a time.
    regular = [i for i, is_low in enumerate(low) if not is_low] or [0]
    for k in range(remaining - sum(volumes)):
        volumes[regular[k % len(regular)]] += 1
    for i, is_low in enumerate(low):
        if is_low:
            volumes[i] = min(LOW_VOLUME_CLAIMS, total)
    return volumes


def generate_claims(
    rng: random.Random,
    providers: list[Provider],
    total: int,
    start: date,
    end: date,
) -> Iterator[list[str]]:
    span_days = (end - start).days
    orgs_by_state: dict[str, list[str]] = {}
    for p in providers:
        if p.entity_type == "Organization":
            orgs_by_state.setdefault(p.state, []).append(p.npi)

    claim_no = 0
    volumes = _claim_volumes(rng, providers, total)
    assert len(volumes) == len(providers)
    for idx, (provider, volume) in enumerate(zip(providers, volumes)):  # noqa: B905 - lengths checked; 3.9-safe
        if volume <= 0:
            continue
        # Each provider sees a panel of members; repeat visits are common.
        panel = [f"M{idx:05d}{k:05d}" for k in range(max(3, volume // 3))]
        # Individuals often bill under a group in their state; otherwise they bill for themselves.
        groups = orgs_by_state.get(provider.state, [])
        billing_npi = (
            rng.choice(groups)
            if provider.entity_type == "Individual" and groups and rng.random() < 0.6
            else provider.npi
        )
        lo, hi = provider.billed_range
        for _ in range(volume):
            claim_no += 1
            service = start + timedelta(days=rng.randint(0, span_days))
            submitted = min(service + timedelta(days=rng.randint(0, 30)), end)
            status = rng.choices(STATUSES, STATUS_WEIGHTS)[0]
            billed = round(rng.uniform(lo, hi), 2)
            paid = round(billed * rng.uniform(0.35, 0.9), 2) if status in ("PAID", "ADJUSTED") else 0.0
            yield [
                f"C{claim_no:09d}",
                provider.npi,
                billing_npi,
                rng.choice(panel),
                service.isoformat(),
                submitted.isoformat(),
                status,
                f"{billed:.2f}",
                f"{paid:.2f}",
            ]


def write_csvs(
    out_dir: Path,
    *,
    providers: int,
    claims: int,
    seed: int,
    start: date,
    end: date,
) -> tuple[Path, Path, int]:
    if providers < len(SAMPLE_NPIS):
        raise ValueError(f"--providers must be at least {len(SAMPLE_NPIS)} (the demo NPIs)")
    if claims < 0:
        raise ValueError("--claims must be >= 0")
    if start > end:
        raise ValueError("--start-date must be on or before --end-date")

    rng = random.Random(seed)  # noqa: S311 - deterministic fake data, not security
    out_dir.mkdir(parents=True, exist_ok=True)
    provider_rows = generate_providers(rng, providers)

    providers_path = out_dir / "providers.csv"
    with providers_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(PROVIDER_COLUMNS)
        writer.writerows(p.row() for p in provider_rows)

    claims_path = out_dir / "claims.csv"
    written = 0
    with claims_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CLAIM_COLUMNS)
        for row in generate_claims(rng, provider_rows, claims, start, end):
            writer.writerow(row)
            written += 1
    return providers_path, claims_path, written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--providers", type=int, default=100, help="number of providers (default 100)")
    parser.add_argument("--claims", type=int, default=10_000, help="number of claims (default 10000)")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--out-dir", type=Path, default=Path("data/fake"))
    args = parser.parse_args(argv)

    try:
        providers_path, claims_path, written = write_csvs(
            args.out_dir,
            providers=args.providers,
            claims=args.claims,
            seed=args.seed,
            start=args.start_date,
            end=args.end_date,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Wrote {args.providers} providers to {providers_path}")
    print(f"Wrote {written} claims to {claims_path}")


if __name__ == "__main__":
    main()
