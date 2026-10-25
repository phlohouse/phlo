"""Prove the preset builds the same report twice from the same pull.

This is the end-to-end check the README points at. It builds twice through
``python -m source_to_report``, compares every reader-facing artefact by
hash, and checks the results against the generator's manifest. It writes
``verification.json`` next to the builds and exits non-zero if anything is
off.

Run it with ``uv run python scripts/verify.py``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from source_to_report import paths

ROOT = Path(__file__).resolve().parents[1]


def _digest(root: Path) -> dict[str, str]:
    files = [root / "index.html", root / "summary.json"]
    files += sorted((root / "data").glob("*.csv"))
    files += sorted((root / "charts").glob("*.svg"))
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _build(database: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "source_to_report",
            "--db",
            str(database),
            "--out",
            str(output),
            "--quiet",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def verify(out_dir: Path, *, quiet: bool) -> int:
    """Build twice, compare, and return an exit code."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = paths.data_dir()
    manifest = json.loads((data_dir / "raw" / "manifest.json").read_text(encoding="utf-8"))

    builds = {}
    for label in ("a", "b"):
        output = out_dir / label
        database = out_dir / f"{label}.duckdb"
        if not quiet:
            print(f"  build {label}  {output}")
        result = _build(database, output)
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            print(f"FAIL build {label} exited {result.returncode}")
            return 1
        builds[label] = output

    first = _digest(builds["a"])
    second = _digest(builds["b"])
    summary = json.loads((builds["a"] / "summary.json").read_text(encoding="utf-8"))
    claims = _rows(builds["a"] / "data" / "claims_checked.csv")
    annual = _rows(builds["a"] / "data" / "annual_station.csv")
    report = (builds["a"] / "index.html").read_text(encoding="utf-8")
    provenance = json.loads((builds["a"] / "provenance.json").read_text(encoding="utf-8"))

    expected_errors = {
        (error["month"], error["station_id"], error["pollutant"])
        for error in manifest["injected_errors"]
    }
    differing = {
        (row["month"][:7], row["station_id"], row["pollutant"])
        for row in claims
        if row["verdict"] == "differs"
    }
    annual_means = {
        (row["station_id"], row["pollutant"]): float(row["mean_value"]) for row in annual
    }
    annual_ok = all(
        abs(annual_means[(station, pollutant)] - value) <= 1e-6
        for station, pollutants in manifest["annual_means"].items()
        for pollutant, value in pollutants.items()
    )

    checks = [
        ("two builds are identical", first == second, f"{len(first)} reader-facing files"),
        (
            "readings match the pull",
            summary["counts"]["readings"] == manifest["readings"],
            f"{manifest['readings']:,}",
        ),
        ("claim count matches", len(claims) == manifest["claims"], f"{len(claims)}"),
        (
            "only the seeded figures differ",
            differing == expected_errors,
            f"{len(differing)} differing, {len(expected_errors)} seeded",
        ),
        ("annual means match the generator", annual_ok, "within 1e-6"),
        (
            "all summary checks pass",
            all(check["ok"] for check in summary["checks"]),
            f"{len(summary['checks'])} checks",
        ),
        (
            "report is self-contained",
            all(marker not in report for marker in ("<script", "<link", 'src="http', 'href="http')),
            f"{len(report) / 1024:.0f} KB",
        ),
        (
            "every seeded source line is in the report",
            all(
                f"{row['source_path']}:{row['line']}" in report
                for row in claims
                if row["verdict"] == "differs"
            ),
            f"{len(differing)} lines",
        ),
        (
            "provenance pins every input",
            len(provenance["inputs"]) == 13
            and all(len(item["sha256"]) == 64 for item in provenance["inputs"]),
            "1 pull + 12 notes",
        ),
    ]

    if not quiet:
        print()
        print(f"  {'CHECK':<42}{'RESULT':<8}DETAIL")
        for name, ok, detail in checks:
            print(f"  {name:<42}{'PASS' if ok else 'FAIL':<8}{detail}")
        print()

    document = {
        "ok": all(ok for _, ok, _ in checks),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checks": [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks],
        "hashes": {"a": first, "b": second},
    }
    (out_dir / "verification.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"  {passed}/{len(checks)} checks passed -> {out_dir / 'verification.json'}")
    return 0 if document["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    """Run the verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "verification")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    return verify(args.out.resolve(), quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
