"""Live engine + Dagster materialisation through the provider.

Runs only when a real ``phlo-transform`` binary is available (set
``PHLO_TRANSFORM_BIN`` or install it on ``PATH``). The rest of the package
suite stubs the binary; this test proves the orchestration boundary end to
end: workspace discovery, asset specs, the Dagster adapter, a real model
build, and the resulting materialization metadata.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from phlo_transform.assets import build_transform_asset_specs

pytestmark = pytest.mark.integration


def _binary() -> str:
    found = os.environ.get("PHLO_TRANSFORM_BIN") or shutil.which("phlo-transform")
    if not found or not Path(found).is_file():
        # reason: a real engine binary is required; the unit suite covers stubs.
        pytest.skip("phlo-transform binary not available")
    return found


def test_dagster_materialises_a_workspace_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter turns a model build and its tests into one materialization."""

    dg = pytest.importorskip("dagster")
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    workspace = tmp_path / "workflows" / "transforms" / "native"
    (workspace / "transforms").mkdir(parents=True)
    (workspace / "phlo.toml").write_text('[transform]\ndefault_namespace = "main"\n')
    (workspace / "transforms" / "events.sql").write_text("select 1 as id\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_TRANSFORM_BIN", _binary())
    monkeypatch.setenv("PHLO_TRANSFORM_ROOT", str(workspace))
    monkeypatch.setenv("PHLO_TRANSFORM_ADAPTER", "duckdb")
    monkeypatch.setenv("PHLO_TRANSFORM_DUCKDB_PATH", str(tmp_path / "local.duckdb"))

    specs = build_transform_asset_specs(tmp_path)

    assert [spec.key for spec in specs] == ["events"]
    definitions = DagsterOrchestratorAdapter().build_definitions(
        assets=specs, checks=[], resources=[]
    )
    result = dg.materialize(
        assets=list(definitions.assets),
        selection=[dg.AssetKey("events")],
    )

    assert result.success
    materializations = list(result.get_asset_materialization_events())
    assert len(materializations) == 1
    metadata = materializations[0].materialization.metadata
    assert "main.events" in metadata["transform_model"].text
    assert metadata["transform_status"].text == "passed"
