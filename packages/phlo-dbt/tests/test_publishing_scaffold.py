"""Tests publishing-config scaffolding idempotence.

Re-scaffolding must preserve user-customized fields (description, tables,
dependencies) and only append entries for models not yet present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

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
    config = yaml.safe_load(output_path.read_text())
    entry = config["publishing"]["demo"]
    assert set(entry["tables"]) == {"mrt_a"}
    assert entry["tables"]["mrt_a"] == "ref:mrt_a"
    assert entry["dependencies"] == ["mrt_a"]


@pytest.mark.parametrize(
    ("schema_flag", "expected_table"),
    [
        ("--physical-schema", "gold.mrt_a"),
        ("--iceberg-schema", "gold.mrt_a"),
    ],
    ids=["physical-schema", "legacy-iceberg-schema"],
)
def test_scaffold_command_maps_tables_to_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema_flag: str, expected_table: str
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
            schema_flag,
            "gold",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (
        yaml.safe_load((tmp_path / "publishing.yaml").read_text())["publishing"]["demo"]["tables"][
            "mrt_a"
        ]
        == expected_table
    )
