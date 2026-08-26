"""Generate deterministic healthcare claims, eligibility, and provider fixtures.

- ``inbound/claims/claims-<date>.csv``: daily claim arrival files. Some claims
  arrive in multiple versions; raw ingestion appends, and the normalize stage
  collapses each claim to its highest version.
- ``inbound/eligibility/eligibility.csv``: pipe-delimited coverage periods per
  member (``member_id|plan|payer|effective_start|effective_end``).
- ``inbound/providers/providers.json``: provider directory.
- ``failures/``: labeled invalid files, each breaking one named invariant
  (amount reconciliation, duplicate versions, uncovered service dates,
  overlapping eligibility periods).

Amounts follow fixed arithmetic: ``allowed = round(billed * 0.80, 2)`` and
``paid = round(allowed * 0.90, 2)``, so reconciliation holds exactly.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

ARRIVAL_DATES = ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]
CLAIMS_PER_DAY = 8
MEMBER_COUNT = 12
PROVIDERS = {
    "prv-001": ("Northside Family Practice", "family_medicine", "in_network", "1234567890"),
    "prv-002": ("Lakeview Cardiology", "cardiology", "in_network", "2345678901"),
    "prv-003": ("Summit Orthopedics", "orthopedics", "out_of_network", "3456789012"),
    "prv-004": ("Riverbend Radiology", "radiology", "in_network", "4567890123"),
    "prv-005": ("Cedar Dermatology", "dermatology", "out_of_network", "5678901234"),
}
PROCEDURE_CODES = ["99213", "99214", "87070", "73030", "93000", "11102", "96372", "70553"]
PLANS = ["ppo", "hmo", "medicare"]

ELIGIBILITY_FILE = "eligibility.csv"


def member_id(index: int) -> str:
    return f"mbr-{index + 1:03d}"


def service_date(arrival_index: int) -> str:
    """Service occurs two days before the claim file arrives."""
    return ARRIVAL_DATES[max(0, arrival_index - 1)]


def build_claim(sequence: int, arrival_index: int, version: int = 1) -> dict[str, object]:
    member = member_id(sequence % MEMBER_COUNT)
    provider = f"prv-{sequence % len(PROVIDERS) + 1:03d}"
    codes = "|".join(sorted({PROCEDURE_CODES[sequence % 8], PROCEDURE_CODES[(sequence + 3) % 8]}))
    billed = round(120.00 + sequence * 7.25, 2)
    allowed = round(billed * 0.80, 2)
    paid = round(allowed * 0.90, 2)
    return {
        "claim_version_key": f"clm-{sequence:04d}-v{version}",
        "claim_id": f"clm-{sequence:04d}",
        "version": version,
        "member_id": member,
        "provider_id": provider,
        "service_date": f"{service_date(arrival_index)}T00:00:00Z",
        "procedure_codes": codes,
        "billed_amount": billed if version == 1 else round(billed - 25.00, 2),
        "allowed_amount": allowed if version == 1 else round((billed - 25.00) * 0.80, 2),
        "paid_amount": paid if version == 1 else round((billed - 25.00) * 0.72, 2),
    }


def build_claims() -> dict[str, list[dict[str, object]]]:
    """Daily arrival files; every tenth claim re-files a corrected version."""
    files: dict[str, list[dict[str, object]]] = {}
    sequence = 0
    for arrival_index, date in enumerate(ARRIVAL_DATES):
        rows: list[dict[str, object]] = []
        for _slot in range(CLAIMS_PER_DAY):
            sequence += 1
            rows.append(build_claim(sequence, arrival_index))
            if sequence % 10 == 0:
                rows.append(build_claim(sequence, arrival_index, version=2))
        files[date] = rows
    return files


def build_eligibility() -> list[dict[str, str]]:
    """One or two non-overlapping coverage periods per member."""
    rows: list[dict[str, str]] = []
    for index in range(MEMBER_COUNT):
        member = member_id(index)
        plan = PLANS[index % len(PLANS)]
        payer = f"payer-{index % 2 + 1}"
        start = "2026-01-01T00:00:00Z"
        end = "2099-12-31T00:00:00Z"
        rows.append(
            {
                "eligibility_key": f"{member}|{start[:10]}",
                "member_id": member,
                "plan": plan,
                "payer": payer,
                "effective_start": start,
                "effective_end": end,
            }
        )
        if index % 4 == 0:
            # A prior plan period that ended before the current one began.
            rows.append(
                {
                    "eligibility_key": f"{member}|2025-01-01",
                    "member_id": member,
                    "plan": "hmo",
                    "payer": payer,
                    "effective_start": "2025-01-01T00:00:00Z",
                    "effective_end": "2025-12-31T00:00:00Z",
                }
            )
    return rows


def build_providers() -> list[dict[str, str]]:
    return [
        {
            "provider_id": provider_id,
            "name": name,
            "specialty": specialty,
            "npi": npi,
            "network_status": status,
        }
        for provider_id, (name, specialty, status, npi) in PROVIDERS.items()
    ]


def _write_claims_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_inbound(data: Path) -> None:
    for date, rows in build_claims().items():
        _write_claims_csv(data / "inbound" / "claims" / f"claims-{date}.csv", rows)

    eligibility = data / "inbound" / ELIGIBILITY_FILE.removesuffix(".csv")
    eligibility.mkdir(parents=True, exist_ok=True)
    with (eligibility / ELIGIBILITY_FILE).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "eligibility_key",
                "member_id",
                "plan",
                "payer",
                "effective_start",
                "effective_end",
            ],
            delimiter="|",
        )
        writer.writeheader()
        writer.writerows(build_eligibility())

    providers = data / "inbound" / "providers"
    providers.mkdir(parents=True, exist_ok=True)
    (providers / "providers.json").write_text(
        json.dumps(build_providers(), indent=2), encoding="utf-8"
    )


def _write_failures(data: Path) -> None:
    """Labeled invalid files; each breaks exactly one named invariant."""
    failures = data / "failures"
    failures.mkdir()

    breach = build_claim(3, 1)
    breach["paid_amount"] = round(float(breach["billed_amount"]) + 10.00, 2)
    breach["claim_version_key"] = "fb-breach-v1"
    breach["claim_id"] = "fb-breach"
    _write_claims_csv(failures / "claims_amount_breach.csv", [breach])

    duplicated = build_claim(5, 1)
    duplicated["claim_version_key"] = "fb-dup-v2"
    duplicated["claim_id"] = "fb-dup"
    duplicated["version"] = 2
    twin = dict(duplicated, claim_version_key="fb-dup-v2-again")
    _write_claims_csv(failures / "claims_duplicate_version.csv", [duplicated, twin])

    uncovered = build_claim(7, 1)
    uncovered["service_date"] = "2024-06-01T00:00:00Z"
    uncovered["claim_version_key"] = "fb-uncovered-v1"
    uncovered["claim_id"] = "fb-uncovered"
    _write_claims_csv(failures / "claims_outside_eligibility.csv", [uncovered])

    periods = build_eligibility()
    overlap = dict(periods[0], eligibility_key="fb-overlap", effective_start="2026-03-01T00:00:00Z")
    with (failures / "eligibility_overlap.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(overlap), delimiter="|")
        writer.writeheader()
        writer.writerows([periods[0], overlap])


def generate(data: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Regenerate every fixture under ``data`` and return summary counts."""
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    _write_inbound(data)
    _write_failures(data)
    claims = build_claims()
    raw_rows = sum(len(rows) for rows in claims.values())
    return {
        "arrival_files": len(ARRIVAL_DATES),
        "raw_claims": raw_rows,
        "latest_claims": len({row["claim_id"] for rows in claims.values() for row in rows}),
        "members": MEMBER_COUNT,
        "providers": len(PROVIDERS),
        "eligibility_periods": len(build_eligibility()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    print(generate(args.data_dir))
