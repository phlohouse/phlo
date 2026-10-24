"""Portable source generation proves the grains used by the Observatory acceptance fixture."""

from __future__ import annotations

import csv
import importlib.util
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mission_control_fixture_generator", ROOT / "scripts/generate_fixtures.py"
)
assert SPEC and SPEC.loader
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)
AS_OF = datetime(2026, 10, 22, 18, tzinfo=UTC)


def test_generation_is_deterministic_and_preserves_actual_scenario_grains(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    counts = GENERATOR.generate(first, as_of=AS_OF)
    GENERATOR.generate(second, as_of=AS_OF)
    assert counts == {
        "bioreactor_telemetry": 2880,
        "lims_samples": 96,
        "elisa_plate_reads": 384,
        "erp_batches": 24,
        "stability": 1284,
    }
    for path in (first / "data").iterdir():
        assert path.read_bytes() == (second / "data" / path.name).read_bytes()
    qc = pd.read_csv(first / "data/lims_samples.csv")
    failed = ~qc.result_value.between(qc.spec_lower, qc.spec_upper)
    assert failed.sum() == 37 and qc[failed].batch_id.nunique() == 10
    telemetry = pd.read_csv(first / "data/bioreactor_telemetry.csv")
    assert not telemetry.duplicated(["vessel_id", "ts"]).any()
    assert telemetry.ph.between(6, 8).all()
    groups = telemetry.assign(minute=pd.to_datetime(telemetry.ts).dt.floor("min")).groupby(
        ["run_id", "vessel_id", "minute"]
    )
    assert len(groups) == 504
    assert (AS_OF - pd.to_datetime(telemetry.ts).max()).total_seconds() > 3600
    plates = pd.read_csv(first / "data/elisa_plate_reads.csv")
    assert set(plates.groupby("sample_id").size()) == {4}


def test_stability_keeps_mixed_dates_original_text_and_exact_qualifier_counts(tmp_path):
    GENERATOR.generate(tmp_path, as_of=AS_OF)
    with (tmp_path / "data/stability_CGT-04_T6M.csv").open() as stream:
        reader = csv.DictReader(stream)
        assert len(reader.fieldnames) == 11
        rows = list(reader)
    assert len(rows) == len({row["sample_id"] for row in rows}) == 1284
    assert Counter(row["pull_date"] for row in rows) == {"24/09/2026": 1201, "2026-09-24": 83}
    flags = Counter(row["result"] for row in rows if row["result"] in {"ND", "<LOQ"})
    assert flags == {"ND": 23, "<LOQ": 23}
    assert rows[2]["result"] == "<LOQ" and rows[6]["result"] == "ND"


def test_example_declares_fourteen_assets_six_checks_eight_jobs_without_runtime_io():
    import dagster as dg

    specification = importlib.util.spec_from_file_location(
        "mission_control_portable_workflow", ROOT / "workflows/mission_control.py"
    )
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    dg.Definitions.validate_loadable(module.defs)
    assert len(module.defs.assets) == 14
    assert len(module.defs.asset_checks) == 6
    assert len(module.defs.jobs) == 8
    assert not module.defs.schedules


def test_all_fourteen_fixture_computations_reconcile_without_catalog_writes(tmp_path, monkeypatch):
    GENERATOR.generate(tmp_path, as_of=AS_OF)
    specification = importlib.util.spec_from_file_location(
        "mission_control_portable_computations", ROOT / "workflows/mission_control.py"
    )
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    tables = {}
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "read", lambda name: tables[name].copy())
    for relation, _, _, _, compute in module.SPECS:
        tables[relation] = compute()
    assert len(tables) == 14
    assert len(tables["silver.bioreactor_runs_1min"]) == 504
    assert len(tables["silver.qc_results"]) == 96
    assert len(tables["silver.elisa_results"]) == 96
    dashboard = tables["gold.qc_release_dashboard"].set_index("release_status")
    assert dashboard.loc["on_hold", "batches"] == 10
    assert dashboard.loc["on_hold", "results_held"] == 37
    assert dashboard.loc["released", "batches"] == 14
    assert dashboard.loc["released", "results_held"] == 0
