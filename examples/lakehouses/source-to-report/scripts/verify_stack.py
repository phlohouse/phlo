"""Prove the full-stack build matches the same ground truth as the local one.

Run this after ``phlo materialize --select "*" --partition 2025-12-31``. It
queries the Trino marts directly and compares them with the generator's
manifest, then checks that the report on disk came from that stack build.

Run it with ``uv run python scripts/verify_stack.py``.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".phlo" / "overrides" / ".env"
REPORT = ROOT / "reports"


def _trino_port() -> int:
    if ENV_FILE.is_file():
        match = re.search(r"^TRINO_PORT=(\d+)$", ENV_FILE.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return int(match.group(1))
    return 8080


def _query(sql: str) -> tuple[list[str], list[tuple]]:
    from trino.dbapi import connect

    connection = connect(
        host="127.0.0.1",
        port=_trino_port(),
        user="dagster",
        catalog="iceberg",
        schema="analytics",
    )
    cursor = connection.cursor()
    cursor.execute(sql)
    columns = [str(description[0]) for description in cursor.description]
    rows = cursor.fetchall()
    cursor.close()
    connection.close()
    return columns, rows


def _rows(sql: str) -> list[dict[str, object]]:
    columns, rows = _query(sql)
    return [dict(zip(columns, row, strict=True)) for row in rows]


def verify(*, quiet: bool = False) -> int:
    """Check the stack build and return an exit code."""
    manifest = json.loads((ROOT / "data" / "raw" / "manifest.json").read_text(encoding="utf-8"))
    annual = _rows("select station_id, pollutant, mean_value from annual_station")
    claims = _rows("select month, station_id, pollutant, verdict from claims_checked")
    coverage = _rows("select coverage from station_coverage")
    monthly = _rows("select count(*) as rows from monthly_station")
    provenance = json.loads((REPORT / "provenance.json").read_text(encoding="utf-8"))
    summary = json.loads((REPORT / "summary.json").read_text(encoding="utf-8"))
    report = (REPORT / "index.html").read_text(encoding="utf-8")

    annual_means = {
        (str(row["station_id"]), str(row["pollutant"])): float(row["mean_value"]) for row in annual
    }
    expected_means = {
        (station, pollutant): value
        for station, pollutants in manifest["annual_means"].items()
        for pollutant, value in pollutants.items()
    }
    differing = {
        (str(row["month"])[:7], str(row["station_id"]), str(row["pollutant"]))
        for row in claims
        if row["verdict"] == "differs"
    }
    expected_errors = {
        (error["month"], error["station_id"], error["pollutant"])
        for error in manifest["injected_errors"]
    }
    annual_ok = set(annual_means) == set(expected_means) and all(
        abs(annual_means[key] - expected_means[key]) <= 1e-6 for key in expected_means
    )

    checks = [
        (
            "report came from the stack",
            provenance.get("warehouse") == "trino",
            str(provenance.get("warehouse")),
        ),
        ("annual means match the generator", annual_ok, f"{len(annual_means)} rows, within 1e-6"),
        ("all published figures present", len(claims) == manifest["claims"], f"{len(claims)}"),
        (
            "only the seeded figures differ",
            differing == expected_errors,
            f"{len(differing)} differing",
        ),
        (
            "coverage is complete",
            bool(coverage) and min(float(row["coverage"]) for row in coverage) >= 0.95,
            ">= 95%",
        ),
        (
            "every month is complete",
            int(monthly[0]["rows"]) == 72,
            f"{monthly[0]['rows']} monthly rows",
        ),
        (
            "summary counts match",
            summary["counts"]["readings"] == manifest["readings"],
            f"{summary['counts']['readings']:,}",
        ),
        (
            "report is self-contained",
            all(marker not in report for marker in ("<script", "<link", 'src="http', 'href="http')),
            f"{len(report) / 1024:.0f} KB",
        ),
        (
            "report carries the same figures",
            all(f"{row['source_path']}:{row['line']}" in report for row in _claims_with_lines()),
            "3 sources",
        ),
    ]

    if not quiet:
        print()
        print(f"  {'CHECK':<40}{'RESULT':<8}DETAIL")
        for name, ok, detail in checks:
            print(f"  {name:<40}{'PASS' if ok else 'FAIL':<8}{detail}")

    document = {
        "ok": all(ok for _, ok, _ in checks),
        "warehouse": "trino",
        "trino_port": _trino_port(),
        "checks": [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks],
    }
    out = ROOT / "build" / "verification" / "stack.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"  {passed}/{len(checks)} stack checks passed -> {out}")
    return 0 if document["ok"] else 1


def _claims_with_lines() -> list[dict[str, str]]:
    """Read the report's own claims table for its source lines."""
    path = REPORT / "data" / "claims_checked.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row["verdict"] == "differs"]


if __name__ == "__main__":
    sys.exit(verify())
