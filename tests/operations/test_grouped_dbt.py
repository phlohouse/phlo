"""Exercise grouped dbt and Dagster on a disposable local DuckDB project.

Results retain per-model identities and checks across subsets and failures.
The test never uses service-backed profile generation or persistence hooks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.filterwarnings(
    r"ignore:datetime.datetime.utcfromtimestamp\(\) is deprecated:"
    r"DeprecationWarning:dagster\._core\.storage\.event_log\.sql_event_log"
)
def test_grouped_dbt_builds_real_models_with_partial_selection_and_failure(monkeypatch, tmp_path):
    # reason: Lightweight lanes may omit either optional adapter.
    dg = pytest.importorskip("dagster")
    pytest.importorskip("dbt.adapters.duckdb")
    from phlo_dagster.adapter import DagsterOrchestratorAdapter
    from phlo_dbt.assets import build_dbt_asset_specs
    from phlo_dbt.settings import DbtSettings

    project = tmp_path / "project"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "dbt_project.yml").write_text(
        "name: grouped\nversion: '1.0'\nprofile: grouped\nmodels:\n  grouped:\n    +materialized: table\n"
    )
    (models / "first.sql").write_text("select 17 as id")
    second = models / "second.sql"
    second.write_text("select id * 3 as amount from {{ ref('first') }}")
    (models / "third.sql").write_text("select id + 11 as value from {{ ref('first') }}")
    (models / "schema.yml").write_text(
        "version: 2\nmodels:\n  - name: first\n    columns:\n      - name: id\n        tests: [unique, not_null]\n"
    )
    profiles = project / "profiles"
    profiles.mkdir()
    database = tmp_path / "data.duckdb"
    (profiles / "profiles.yml").write_text(
        f"grouped:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: {database}\n"
        "      schema: marts\n      threads: 1\n      settings:\n        memory_limit: 64MB\n        threads: 1\n"
    )
    monkeypatch.setenv("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent), prepend=":")
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_profile", lambda *a, **kw: None)
    monkeypatch.setattr("phlo_dbt.transformer.ensure_dbt_profile", lambda *a, **kw: None)
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_manifest", lambda *a, **kw: True)
    monkeypatch.setattr("phlo.hooks.bus.HookBus.emit", lambda *a, **kw: None)
    monkeypatch.setattr(
        "phlo_dbt.assets.get_settings", lambda: DbtSettings(dbt_project_dir=str(project))
    )
    command = [
        str(Path(sys.executable).with_name("dbt")),
        "parse",
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(profiles),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    specs = build_dbt_asset_specs()
    definitions = DagsterOrchestratorAdapter().build_definitions(
        assets=specs, checks=[], resources=[]
    )
    job = definitions.resolve_implicit_global_asset_job_def()
    first = job.execute_in_process(partition_key="2025-01-01")
    assert first.success
    mats = first.get_asset_materialization_events()
    assert {e.asset_key.to_user_string() for e in mats} == {"first", "second", "third"}
    invocation_ids = {
        e.event_specific_data.materialization.metadata["dbt_metadata"].value["invocation_id"]
        for e in mats
    }
    assert len(invocation_ids) == 1 and None not in invocation_ids
    assert len(first.get_asset_check_evaluations()) == 2
    subset = job.execute_in_process(
        partition_key="2025-01-01", asset_selection=[dg.AssetKey("second")]
    )
    assert subset.success
    assert [e.asset_key.to_user_string() for e in subset.get_asset_materialization_events()] == [
        "second"
    ]
    assert not subset.get_asset_check_evaluations()
    second.write_text("select nonexistent from {{ ref('first') }}")
    failed = job.execute_in_process(partition_key="2025-01-01", raise_on_error=False)
    assert not failed.success
    assert {e.asset_key.to_user_string() for e in failed.get_asset_materialization_events()} == {
        "first",
        "third",
    }
    import duckdb

    with duckdb.connect(str(database)) as connection:
        assert connection.execute("select value from marts.third").fetchall() == [(28,)]
    second.write_text("select id * 3 as amount from {{ ref('first') }}")
    (models / "first.sql").write_text("select 17 as id union all select 17 as id")
    invalid = job.execute_in_process(partition_key="2025-01-01", raise_on_error=False)
    assert not invalid.success
    assert invalid.get_asset_materialization_events() == []
    evaluations = invalid.get_asset_check_evaluations()
    assert len(evaluations) == 2
    assert sum(not check.passed for check in evaluations) == 1
    (models / "first.sql").write_text("select 17 as id")
    recovered = job.execute_in_process(partition_key="2025-01-01")
    assert recovered.success
    assert {e.asset_key.to_user_string() for e in recovered.get_asset_materialization_events()} == {
        "first",
        "second",
        "third",
    }
    config = project / "dbt_project.yml"
    config.write_text(config.read_text() + "on-run-end:\n  - 'select nonexistent'\n")
    hook_failed = job.execute_in_process(partition_key="2025-01-01", raise_on_error=False)
    assert not hook_failed.success
    assert {
        e.asset_key.to_user_string() for e in hook_failed.get_asset_materialization_events()
    } == {"first", "second", "third"}
