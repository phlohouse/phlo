"""Tests publishing-config scaffolding idempotence.

Re-scaffolding must preserve user-customized fields (description, tables,
dependencies) and only append entries for models not yet present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phlo_dbt.cli_publishing import scaffold_publishing_config


def _write_manifest(path: Path, model_names: list[str]) -> None:
    """Write a minimal dbt manifest containing model_names as model nodes."""

    nodes = {}
    for name in model_names:
        nodes[f"model.test.{name}"] = {"resource_type": "model", "name": name, "columns": {}}
    path.write_text(json.dumps({"nodes": nodes}))


def test_scaffold_publishing_config_is_idempotent() -> None:
    """Ensure scaffolding preserves custom fields and appends missing models."""

    existing = {
        "publishing": {
            "demo": {
                "name": "publish_demo_marts",
                "group": "publishing",
                "description": "custom description",
                "dependencies": ["mrt_existing"],
                "tables": {"mrt_existing": "marts.mrt_existing"},
            }
        }
    }

    updated = scaffold_publishing_config(
        existing_config=existing,
        model_names=["mrt_existing", "mrt_new"],
        source_key="demo",
        physical_schema="marts",
        group="publishing",
        asset_name="publish_demo_marts",
        description="ignored",
    )

    entry = updated["publishing"]["demo"]
    assert entry["description"] == "custom description"
    assert entry["tables"]["mrt_existing"] == "marts.mrt_existing"
    assert entry["tables"]["mrt_new"] == "marts.mrt_new"
    assert entry["dependencies"] == ["mrt_existing", "mrt_new"]


def test_scaffold_publishing_config_can_emit_logical_refs() -> None:
    updated = scaffold_publishing_config(
        existing_config={},
        model_names=["mrt_orders"],
        source_key="demo",
        physical_schema="marts",
        group="publishing",
        asset_name="publish_demo_marts",
        description="demo",
        logical_refs=True,
    )

    assert updated["publishing"]["demo"]["tables"] == {"mrt_orders": "ref:mrt_orders"}


def test_scaffold_command_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify scaffold CLI writes filtered publishing config."""

    from click.testing import CliRunner

    from phlo_dbt.cli_publishing import publishing

    monkeypatch.chdir(tmp_path)

    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, ["mrt_a", "stg_b"])

    runner = CliRunner()
    result = runner.invoke(
        publishing,
        [
            "scaffold",
            "--manifest",
            str(manifest_path),
            "--output",
            "publishing.yaml",
            "--select",
            "mrt_*",
            "--source",
            "demo",
        ],
    )
    assert result.exit_code == 0, result.output

    output_path = tmp_path / "publishing.yaml"
    contents = output_path.read_text()
    assert "publishing:" in contents
    assert "demo:" in contents
    assert "mrt_a: ref:mrt_a" in contents
    assert "stg_b" not in contents


def test_scaffold_command_can_write_physical_table_mappings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from phlo_dbt.cli_publishing import publishing

    monkeypatch.chdir(tmp_path)

    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, ["mrt_a"])

    result = CliRunner().invoke(
        publishing,
        [
            "scaffold",
            "--manifest",
            str(manifest_path),
            "--output",
            "publishing.yaml",
            "--select",
            "mrt_*",
            "--source",
            "demo",
            "--physical-tables",
            "--physical-schema",
            "gold",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "mrt_a: gold.mrt_a" in (tmp_path / "publishing.yaml").read_text()


def test_scaffold_command_keeps_legacy_iceberg_schema_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from phlo_dbt.cli_publishing import publishing

    monkeypatch.chdir(tmp_path)

    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, ["mrt_a"])

    result = CliRunner().invoke(
        publishing,
        [
            "scaffold",
            "--manifest",
            str(manifest_path),
            "--output",
            "publishing.yaml",
            "--select",
            "mrt_*",
            "--source",
            "demo",
            "--physical-tables",
            "--iceberg-schema",
            "gold",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "mrt_a: gold.mrt_a" in (tmp_path / "publishing.yaml").read_text()
