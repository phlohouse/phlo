"""Prove full refresh removes stale data without changing incremental defaults."""

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from phlo.logging import get_logger
from phlo_dbt.assets import _run_dbt_model
from phlo_dbt.translator import DbtSpecTranslator


def test_dbt_asset_full_refresh_replaces_disposable_incremental_table(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("dbt.adapters.duckdb")
    dbt = shutil.which("dbt")
    if dbt is None:
        # reason: This disposable integration test requires the optional dbt CLI.
        pytest.skip("dbt CLI is not installed")
    (tmp_path / "models").mkdir()
    (tmp_path / "seeds").mkdir()
    (tmp_path / "profiles").mkdir()
    database = tmp_path / "disposable.duckdb"
    (tmp_path / "dbt_project.yml").write_text(
        "name: refresh_test\nprofile: refresh_test\nversion: '1.0'\nconfig-version: 2\n"
    )
    (tmp_path / "profiles" / "profiles.yml").write_text(
        f"refresh_test:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: {database}\n      threads: 1\n"
    )
    (tmp_path / "models" / "orders.sql").write_text(
        "{{ config(materialized='incremental', unique_key='id') }}\nselect * from {{ ref('source_orders') }}\n"
    )
    source = tmp_path / "seeds" / "source_orders.csv"
    source.write_text("id,value\n1,11\n2,29\n")
    runtime = SimpleNamespace(
        partition_key=None,
        tags={},
        logger=get_logger("full-refresh-test"),
        run_id=None,
        resources={},
    )

    def seed():
        result = subprocess.run(
            [dbt, "seed", "--full-refresh", "--profiles-dir", str(tmp_path / "profiles")],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def run():
        result = _run_dbt_model(
            model_name="orders",
            asset_key="orders",
            project_dir=tmp_path,
            profiles_dir=tmp_path / "profiles",
            runtime=runtime,
            manifest={"nodes": {}, "sources": {}},
            translator=DbtSpecTranslator(),
        )
        assert result[-1].status == "success"

    def rows():
        with duckdb.connect(str(database)) as connection:
            return connection.execute("select id, value from orders order by id").fetchall()

    seed()
    run()
    assert rows() == [(1, 11), (2, 29)]
    source.write_text("id,value\n2,31\n3,47\n")
    seed()
    runtime.tags = {"phlo/full_refresh_asset": "another_asset"}
    run()
    assert rows() == [(1, 11), (2, 31), (3, 47)]
    runtime.tags = {"phlo/full_refresh_asset": "orders"}
    run()
    assert rows() == [(2, 31), (3, 47)]
