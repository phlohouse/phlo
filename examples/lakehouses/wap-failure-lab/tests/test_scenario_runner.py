"""Runner regressions without a live catalog, CLI, or optional data libraries."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "lab_runner", Path(__file__).resolve().parents[1] / "scripts/run_scenario.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_wait_ignores_other_terminal_runs(runner, tmp_path):
    (tmp_path / "other.json").write_text(json.dumps({"status": "promoted"}))
    (tmp_path / "wanted.json").write_text(json.dumps({"status": "launched"}))
    assert runner.wait_for_terminal_report("wanted", tmp_path, 0) == (
        "in_flight",
        "wanted",
        {"status": "launched"},
    )
    assert runner.wait_for_terminal_report("absent", tmp_path, 0) == ("missing", "absent", None)


def test_materialize_uses_structured_logical_identity(runner, monkeypatch):
    def execute(command, **kwargs):
        assert command[0] == "/current/checkout/phlo"
        assert command[-1] == "--json"
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"logical_run_id": "logical", "run_id": "dagster"}}),
            stderr="diagnostics",
        )

    monkeypatch.setenv("PHLO_EXECUTABLE", "/current/checkout/phlo")
    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.materialize("asset", "partition") == "logical"


@pytest.mark.parametrize("state", ["in_flight", "missing"])
def test_quality_timeout_is_not_a_success(runner, monkeypatch, state):
    monkeypatch.setattr(runner, "stage_inbound", lambda *_: None)
    monkeypatch.setattr(runner, "materialize", lambda *_: "wanted")
    monkeypatch.setattr(runner, "table_count", lambda *_: 0)
    monkeypatch.setattr(runner, "branch_hash", lambda *_: "hash")
    monkeypatch.setattr(
        runner,
        "wait_for_terminal_report",
        lambda *_: (state, "wanted", {"status": "launched", "branch": "b"}),
    )
    with pytest.raises(runner.ScenarioError, match="did not terminalize"):
        runner.run_quality_failure(runner.LabContext())


def test_concurrent_launches_both_precede_polling_and_conflict_is_retained(runner, monkeypatch):
    launches = []
    counts = iter([5, 0, 0, 12, 0, 17])
    monkeypatch.setattr(runner, "trino_fetchall", lambda *_: [("existing",)])
    monkeypatch.setattr(runner, "stage_inbound", lambda *_: None)
    monkeypatch.setattr(runner, "table_count", lambda *_: next(counts))

    def launch(asset, partition):
        launches.append(partition)
        return partition

    def wait(run_id, *_):
        assert set(launches) == {"2026-08-20", "2026-08-21"}
        promoted = run_id.endswith("20")
        return (
            "promoted" if promoted else "blocked",
            run_id,
            {
                "status": "promoted" if promoted else "promotion_failed",
                "branch": run_id,
                "launch_target_hash_before": "shared-base",
                "target_hash_before": "old",
                "target_hash_after": "new",
            },
        )

    monkeypatch.setattr(runner, "materialize", launch)
    monkeypatch.setattr(runner, "wait_for_terminal_report", wait)
    monkeypatch.setattr(
        runner, "branch_hash", lambda branch: None if branch.endswith("20") else "retained"
    )
    assert runner.run_concurrent_runs(runner.LabContext())["rows_added"] == 12


def test_dbt_executes_build_then_compares_sql(runner, monkeypatch):
    events = []
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **_: events.append(command[1]))

    def query(sql):
        assert events[0] == "build"
        events.append(sql)
        return [("sensor", 3, 3)]

    monkeypatch.setattr(runner, "trino_fetchall", query)
    runner.build_and_check_dbt()
    assert len(events) == 3
    monkeypatch.setattr(
        runner, "trino_fetchall", lambda sql: [("sensor", 3, 3)] if "GROUP BY" in sql else []
    )
    with pytest.raises(runner.ScenarioError, match="aggregate differs"):
        runner.build_and_check_dbt()


def test_valid_publish_requires_dbt_after_promotion(runner, monkeypatch):
    counts = iter([0, 12])
    events = []
    monkeypatch.setattr(runner, "stage_inbound", lambda *_: None)
    monkeypatch.setattr(runner, "table_count", lambda *_: next(counts))
    monkeypatch.setattr(runner, "materialize", lambda *_: "expected")

    def wait(run_id, *_):
        assert run_id == "expected"
        events.append("promoted")
        return (
            "promoted",
            run_id,
            {
                "status": "promoted",
                "branch": "b",
                "target_hash_before": "old",
                "target_hash_after": "new",
            },
        )

    monkeypatch.setattr(runner, "wait_for_terminal_report", wait)
    monkeypatch.setattr(runner, "branch_hash", lambda *_: None)
    monkeypatch.setattr(runner, "build_and_check_dbt", lambda: events.append("dbt"))
    monkeypatch.setattr(runner, "export_and_check_run_evidence", lambda *_: None)
    summary = runner.run_valid_publish(runner.LabContext())
    assert events == ["promoted", "dbt"]
    assert summary["dbt_build_and_sql_verified"] is True


def test_exported_evidence_requires_exact_run_and_observed_sections(runner):
    payload = {"run_id": "run", "terminal_outcome": {"status": "success"}}
    fields = (
        "inputs",
        "staging",
        "outputs",
        "lineage",
        "artifacts",
        "iceberg_snapshots",
        "quality",
        "catalog_changes",
    )
    payload.update({field: [{"resource_identity_status": "complete"}] for field in fields})
    runner.assert_run_evidence(payload, "run")
    with pytest.raises(runner.ScenarioError, match="another run"):
        runner.assert_run_evidence(payload, "other")
    for field in fields:
        with pytest.raises(runner.ScenarioError, match=f"missing {field}"):
            runner.assert_run_evidence({**payload, field: []}, "run")


def test_concurrent_serial_publications_are_rejected(runner, monkeypatch):
    monkeypatch.setattr(runner, "stage_inbound", lambda *_: None)
    monkeypatch.setattr(runner, "table_count", lambda *_: 0)
    monkeypatch.setattr(runner, "trino_fetchall", lambda *_: [])
    monkeypatch.setattr(runner, "materialize", lambda asset, partition: partition)
    monkeypatch.setattr(
        runner,
        "wait_for_terminal_report",
        lambda run_id, *_: (
            "promoted",
            run_id,
            {"status": "promoted", "launch_target_hash_before": run_id},
        ),
    )
    with pytest.raises(runner.ScenarioError, match="did not overlap"):
        runner.run_concurrent_runs(runner.LabContext())
