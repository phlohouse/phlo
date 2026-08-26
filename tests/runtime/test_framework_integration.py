"""
Integration tests for workflow discovery system.

Tests the end-to-end workflow discovery and definitions building
for user projects using Phlo as an installable package.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from dagster import Definitions
from phlo_dagster.framework.definitions import build_definitions
from phlo_dagster.framework.discovery import discover_user_workflows

from phlo.exceptions import PhloConfigError

pytestmark = pytest.mark.integration


def _collect_asset_names(defs: Definitions) -> list[str]:
    names: list[str] = []
    for asset in list(defs.assets or []):
        if hasattr(asset, "keys"):
            names.extend(str(key) for key in asset.keys)  # type: ignore[attr-defined]
        elif hasattr(asset, "key"):
            names.append(asset.key.to_string())  # type: ignore[attr-defined]
    return names


def test_discover_empty_workflows_directory(tmp_path):
    """Test discovering workflows from empty directory."""
    workflows_path = Path(tmp_path) / "workflows"
    workflows_path.mkdir()

    # Should return Definitions (may have dbt/publishing assets from config)
    defs = discover_user_workflows(workflows_path, clear_registries=True)

    assert isinstance(defs, Definitions)
    # Assets may include auto-discovered dbt/publishing assets from project config


def test_discover_workflows_falls_back_when_orchestrator_plugin_missing(tmp_path):
    """Test discovery falls back to local Dagster adapter when entry-point lookup fails."""
    workflows_path = Path(tmp_path) / "workflows"
    workflows_path.mkdir()

    with patch(
        "phlo_dagster.framework.discovery.get_active_orchestrator",
        side_effect=PhloConfigError("Orchestrator adapter 'dagster' is not installed."),
    ):
        defs = discover_user_workflows(workflows_path, clear_registries=True)

    assert isinstance(defs, Definitions)


def test_discover_workflows_with_simple_asset(tmp_path):
    """Test discovering a simple ingestion workflow."""
    workflows_path = Path(tmp_path) / "workflows"
    workflows_path.mkdir()

    # Create a simple workflow file
    ingestion_dir = workflows_path / "ingestion"
    ingestion_dir.mkdir()
    (ingestion_dir / "__init__.py").write_text("")

    # Create a simple ingestion workflow
    workflow_content = '''"""
Simple test workflow.
"""

from phlo_dlt import phlo_ingestion
from dlt.sources.rest_api import rest_api
from pandera.pandas import DataFrameModel


class TestSchema(DataFrameModel):
    id: str


@phlo_ingestion(
    table_name="test_data",
    unique_key="id",
    group="test",
    validation_schema=TestSchema,
)
def test_workflow(partition_date: str):
    """Test workflow for discovery."""
    source = rest_api(
        client={
            "base_url": "https://api.example.com",
        },
        resources=[
            {
                "name": "test",
                "endpoint": {"path": "test"},
            }
        ],
    )
    return source
'''
    (ingestion_dir / "test_workflow.py").write_text(workflow_content)

    # Discover workflows
    defs = discover_user_workflows(workflows_path, clear_registries=True)

    # Should find the asset
    assert isinstance(defs, Definitions)
    assets = list(defs.assets or [])
    assert len(assets) > 0

    # Check that the asset has the correct name
    asset_names = _collect_asset_names(defs)

    assert any("dlt_test_data" in name for name in asset_names)


def test_build_definitions_with_user_workflows(tmp_path):
    """Test building complete definitions with user workflows."""
    workflows_path = Path(tmp_path) / "workflows"
    workflows_path.mkdir()

    # Create minimal workflow structure
    (workflows_path / "__init__.py").write_text("")

    # Build definitions (should work even with empty workflows)
    defs = build_definitions(workflows_path=workflows_path)

    assert isinstance(defs, Definitions)
    # Should at least have resources
    assert defs.resources is not None


def test_discover_workflows_collects_plain_dagster_assets(tmp_path):
    """Test workflow discovery includes module-level Dagster @asset definitions."""
    workflows_path = Path(tmp_path) / "workflows"
    publishing_dir = workflows_path / "publishing"
    publishing_dir.mkdir(parents=True)
    (workflows_path / "__init__.py").write_text("")
    (publishing_dir / "__init__.py").write_text("")
    (publishing_dir / "events.py").write_text(
        """
from dagster import asset

@asset(group_name="publishing")
def publish_demo_marts():
    return {"rows": 1}
"""
    )

    defs = discover_user_workflows(workflows_path, clear_registries=True)

    assert isinstance(defs, Definitions)
    asset_names = _collect_asset_names(defs)
    assert any("publish_demo_marts" in name for name in asset_names)


def test_build_definitions_without_workflows_path(tmp_path):
    """Test that build_definitions handles missing workflows gracefully."""
    non_existent = Path(tmp_path) / "nonexistent"

    # Should not raise, just log warning
    defs = build_definitions(workflows_path=non_existent)

    assert isinstance(defs, Definitions)


def test_cli_init_command_structure(tmp_path):
    """Test that phlo init creates correct project structure."""
    from phlo.cli.main import _create_project_structure

    project_dir = Path(tmp_path) / "test-project"

    _create_project_structure(project_dir, "test-project", "basic")

    # Check that all expected files/directories exist
    assert (project_dir / "workflows").is_dir()
    assert (project_dir / "workflows" / "__init__.py").exists()
    assert (project_dir / "workflows" / "ingestion").is_dir()
    assert (project_dir / "workflows" / "schemas").is_dir()
    assert (project_dir / "workflows" / "transforms" / "dbt").is_dir()
    assert (project_dir / "workflows" / "transforms" / "dbt" / "dbt_project.yml").exists()
    assert (project_dir / "tests").is_dir()
    assert (project_dir / "pyproject.toml").exists()
    assert (project_dir / ".env.example").exists()
    assert (project_dir / ".gitignore").exists()
    assert (project_dir / "README.md").exists()

    # Check pyproject.toml content
    pyproject_content = (project_dir / "pyproject.toml").read_text()
    assert 'name = "test-project"' in pyproject_content
    assert '"phlo"' in pyproject_content


def test_cli_init_minimal_template(tmp_path):
    """Test that minimal template doesn't create dbt structure."""
    from phlo.cli.main import _create_project_structure

    project_dir = Path(tmp_path) / "minimal-project"

    _create_project_structure(project_dir, "minimal-project", "minimal")

    # Should have workflows but not transforms
    assert (project_dir / "workflows").is_dir()
    assert not (project_dir / "workflows" / "transforms").exists()
