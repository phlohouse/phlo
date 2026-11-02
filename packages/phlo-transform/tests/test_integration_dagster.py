"""Execute a disposable native workspace through the real engine and Dagster.

Runs when a local ``phlo-transform`` binary is available. Subsets, unchanged
models, failures and refresh rejection retain per-asset evidence without
external services or modifying any existing workspace.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from phlo_transform.assets import build_transform_asset_specs


def _binary() -> str:
    found = os.environ.get("PHLO_TRANSFORM_BIN") or shutil.which("phlo-transform")
    if not found or not Path(found).is_file():
        # reason: a real engine binary is required; the unit suite covers stubs.
        pytest.skip("phlo-transform binary not available")
    return found


def test_dagster_groups_workspace_assets_and_preserves_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run selected models once without attributing sibling failures to healthy assets."""

    dg = pytest.importorskip("dagster")
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    workspace = tmp_path / "workflows" / "transforms" / "native"
    (workspace / "transforms").mkdir(parents=True)
    (workspace / "phlo.toml").write_text(
        '[transform]\ndefault_namespace = "main"\ndefault_materialization = "table"\n'
    )
    (workspace / "transforms" / "events.sql").write_text("select 17 as id\n", encoding="utf-8")
    totals = workspace / "transforms" / "totals.sql"
    totals.write_text(
        "-- @not-null amount\nselect id * 3 as amount from events\n", encoding="utf-8"
    )
    (workspace / "transforms" / "independent.sql").write_text(
        "select 29 as value\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_TRANSFORM_BIN", _binary())
    monkeypatch.setenv("PHLO_TRANSFORM_ROOT", str(workspace))
    monkeypatch.setenv("PHLO_TRANSFORM_ADAPTER", "duckdb")
    monkeypatch.setenv("PHLO_TRANSFORM_DUCKDB_PATH", str(tmp_path / "local.duckdb"))
    monkeypatch.setenv("PHLO_TRANSFORM_JOBS", "1")
    monkeypatch.setenv("PHLO_TRANSFORM_GROUP_EXECUTION", "true")
    monkeypatch.setenv("PHLO_TRANSFORMATION_PROVIDER", "transform")

    specs = build_transform_asset_specs(tmp_path)

    assert {spec.key for spec in specs} == {"events", "totals", "independent"}
    definitions = DagsterOrchestratorAdapter().build_definitions(
        assets=specs, checks=[], resources=[]
    )
    job = definitions.resolve_implicit_global_asset_job_def()
    result = job.execute_in_process()
    assert result.success
    materializations = list(result.get_asset_materialization_events())
    assert {event.asset_key.to_user_string() for event in materializations} == {
        "events",
        "totals",
        "independent",
    }
    run_ids = {
        event.materialization.metadata["transform_run_id"].text for event in materializations
    }
    assert len(run_ids) == 1
    checks = result.get_asset_check_evaluations()
    assert len(checks) == 1 and checks[0].passed
    import duckdb

    with duckdb.connect(str(tmp_path / "local.duckdb")) as connection:
        assert connection.execute("select amount from main.totals").fetchall() == [(51,)]
        assert connection.execute("select value from main.independent").fetchall() == [(29,)]
    subset = job.execute_in_process(asset_selection=[dg.AssetKey("totals")])
    assert subset.success
    assert [e.asset_key.to_user_string() for e in subset.get_asset_materialization_events()] == [
        "totals"
    ]
    (subset_check,) = subset.get_asset_check_evaluations()
    assert subset_check.asset_key == dg.AssetKey("totals") and subset_check.passed
    unchanged = job.execute_in_process()
    assert unchanged.success
    assert {
        e.materialization.metadata["transform_status"].text
        for e in unchanged.get_asset_materialization_events()
    } == {"skipped"}
    totals.write_text(
        "-- @not-null amount\nselect cast('broken' as bigint) as amount from events\n",
        encoding="utf-8",
    )
    failed = job.execute_in_process(raise_on_error=False)
    assert not failed.success
    assert {e.asset_key.to_user_string() for e in failed.get_asset_materialization_events()} == {
        "events",
        "independent",
    }
    totals.write_text(
        "-- @not-null amount\nselect id * 3 as amount from events\n", encoding="utf-8"
    )
    refresh = job.execute_in_process(
        tags={"phlo/full_refresh_asset": "totals"}, raise_on_error=False
    )
    assert not refresh.success
    failure = refresh.get_step_failure_events()[0].event_specific_data
    assert "Native full refresh is unsupported" in str(failure.error)
    (workspace / "transforms" / "events.sql").write_text(
        "-- @not-null id\nselect cast(null as bigint) as id\n", encoding="utf-8"
    )
    checked_specs = build_transform_asset_specs(tmp_path)
    checked_definitions = DagsterOrchestratorAdapter().build_definitions(
        assets=checked_specs, checks=[], resources=[]
    )
    checked = checked_definitions.resolve_implicit_global_asset_job_def().execute_in_process(
        raise_on_error=False
    )
    assert not checked.success
    assert {e.asset_key.to_user_string() for e in checked.get_asset_materialization_events()} == {
        "independent"
    }
    (check,) = checked.get_asset_check_evaluations()
    assert check.asset_key == dg.AssetKey("events") and not check.passed
    with duckdb.connect(str(tmp_path / "local.duckdb")) as connection:
        assert connection.execute("select amount from main.totals").fetchall() == [(51,)]
