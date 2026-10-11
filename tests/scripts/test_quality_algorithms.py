"""Exercise benchmark worker contracts with real Parquet and DuckDB inputs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "quality_algorithms", ROOT / "scripts/benchmarks/quality_algorithms.py"
)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize(
    "scenario", ["validation", "read_once", "aggregates", "grouped", "merge_keys"]
)
def test_worker_reports_independent_quality_outcomes(
    tmp_path, monkeypatch, capsys, scenario, optimized
):
    rows = 100_000 if scenario == "grouped" else 100_002
    digest = benchmark.fixture(tmp_path, rows)
    assert len(digest) == 64
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setenv("PHLO_NO_AUTO_DISCOVER", "1")
    argv = [
        "quality_algorithms.py",
        "--worker",
        "--root",
        str(root),
        "--fixture",
        str(tmp_path),
        "--scenario",
        scenario,
        "--rows",
        str(rows),
        "--batch-size",
        "4096",
    ]
    if optimized:
        argv.append("--optimized")
    monkeypatch.setattr(sys, "argv", argv)
    benchmark.main()
    record = json.loads(
        next(
            line[10:]
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("BENCHMARK ")
        )
    )
    assert record["scenario"] == scenario
    assert record["root"] == str(root)
    assert record["seconds"] >= 0
    assert record["peak_rss_mib"] > 0
    expected = {
        "validation": {"total": rows, "failures": 1, "failure_index": rows - 1},
        "read_once": {"total": rows, "checks": 3, "reads": 2},
        "aggregates": {"total": rows, "duplicates": rows, "range_failures": 1},
        "grouped": {"total": rows, "mismatches": 1},
        "merge_keys": {"unique_keys": 50_001, "checksum": 1_250_025_000},
    }[scenario]
    assert {key: record[key] for key in expected} == expected
    assert record["loaded_modules"]
    assert all(Path(path).is_relative_to(root) for path in record["loaded_modules"].values())


def test_compare_reports_both_real_worker_variants(tmp_path, monkeypatch):
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "quality_algorithms.py",
            "--root",
            str(ROOT),
            "--baseline",
            str(ROOT),
            "--rows",
            "24",
            "--scenarios",
            "read_once",
            "--repeats",
            "1",
            "--output",
            str(output),
        ],
    )
    benchmark.main()
    report = json.loads(output.read_text())
    assert report["rows"] == 24
    assert len(report["fixture_sha256"]) == 64
    assert [record["variant"] for record in report["records"]] == ["before", "after"]
    for record in report["records"]:
        assert record["scenario"] == "read_once"
        assert (record["total"], record["checks"], record["reads"], record["repeat"]) == (
            24,
            3,
            1,
            0,
        )
        assert record["loaded_modules"]
        assert all(Path(path).is_relative_to(ROOT) for path in record["loaded_modules"].values())
