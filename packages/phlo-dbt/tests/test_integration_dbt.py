"""Integration tests for phlo-dbt.

Runs dbt against a temporary project to exercise CLI execution, direct
DbtTransformer execution, and project parsing. Skips cleanly when the duckdb
adapter is missing from the environment.
"""

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
import yaml

from phlo.logging import get_logger

pytestmark = pytest.mark.integration


def _is_missing_duckdb_adapter(output: str) -> bool:
    normalized = output.lower()
    patterns = (
        "could not find adapter type duckdb",
        "adapter type duckdb is not installed",
        "no module named 'dbt.adapters.duckdb'",
        "module not found: dbt.adapters.duckdb",
        "adapter not found",
    )
    return "duckdb" in normalized and any(pattern in normalized for pattern in patterns)


def _skip_if_duckdb_adapter_missing(
    dbt_executable: str, project_dir: Path, env: dict[str, str]
) -> None:
    try:
        debug_result = subprocess.run(
            [dbt_executable, "debug", "--profiles-dir", str(project_dir)],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        pytest.fail(f"dbt debug timed out after 60s\nstdout:\n{stdout}\nstderr:\n{stderr}")
    combined_output = "\n".join((debug_result.stdout, debug_result.stderr))
    if _is_missing_duckdb_adapter(combined_output):
        pytest.skip("dbt-duckdb adapter not installed")


@pytest.fixture
def dbt_project_dir(tmp_path):
    """Create a temporary dbt project structure."""
    # Find dbt executable in the same directory as the python executable
    import sys

    bin_dir = Path(sys.executable).parent
    dbt_path = bin_dir / "dbt"

    if not dbt_path.exists():
        # Fallback to PATH
        dbt_path = shutil.which("dbt")

    if dbt_path is None:
        pytest.skip("dbt CLI not available")

    # Use strict string path ensuring we have the absolute path
    dbt_executable = str(dbt_path)

    project_name = "test_project"
    project_dir = tmp_path / project_name
    project_dir.mkdir()

    # dbt_project.yml
    dbt_project = {
        "name": project_name,
        "version": "1.0.0",
        "config-version": 2,
        "profile": "test_profile",
        "model-paths": ["models"],
        "models": {project_name: {"example": {"materialized": "view"}}},
    }
    (project_dir / "dbt_project.yml").write_text(yaml.dump(dbt_project))

    # profiles.yml - Use DuckDB for isolated testing if available
    profiles = {
        "test_profile": {
            "target": "dev",
            "outputs": {
                "dev": {
                    "type": "duckdb",
                    "path": str(project_dir / "test.duckdb"),
                    "threads": 1,
                }
            },
        }
    }

    (project_dir / "profiles.yml").write_text(yaml.dump(profiles))

    models_dir = project_dir / "models" / "example"
    models_dir.mkdir(parents=True)

    (models_dir / "my_first_dbt_model.sql").write_text("select 1 as id")

    return project_dir, dbt_executable


def test_dbt_transformer_execution(dbt_project_dir):
    """Test DbtTransformer execution directly (orchestrator-agnostic)."""
    project_dir, dbt_exe = dbt_project_dir

    # Set DBT_PROFILES_DIR
    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(project_dir)
    _skip_if_duckdb_adapter_missing(dbt_exe, project_dir, env)

    # Import and use DbtTransformer
    from phlo_dbt.transformer import DbtTransformer

    logger = get_logger("test_dbt")

    transformer = DbtTransformer(
        context=None,
        logger=logger,
        project_dir=project_dir,
        profiles_dir=project_dir,
        target="dev",
        dbt_executable=dbt_exe,
    )

    # Run transformation
    result = transformer.run_transform()

    # Verify result
    assert result.status == "success", f"DbtTransformer failed: {result.error}"
    assert "dbt_output" in result.metadata
    assert not (project_dir / "target" / "catalog.json").exists()


def test_isolated_builds_reuse_parsing_and_validate_changed_inputs(dbt_project_dir):
    """Real dbt owns cache invalidation; Phlo must never reuse build outputs."""
    import json

    from phlo_dbt.transformer import DbtTransformer

    project, executable = dbt_project_dir
    _skip_if_duckdb_adapter_missing(executable, project, os.environ.copy())
    model = project / "models" / "example" / "my_first_dbt_model.sql"
    model.write_text("select {{ var('partition_date_str', '3') }} as id")
    snapshots = []

    def build(run, partition=None, target_name="dev", schema="main", **parameters):
        transformer = DbtTransformer(
            context=SimpleNamespace(run_id=run, asset_key="my_first_dbt_model"),
            logger=get_logger("test_parse_cache"),
            project_dir=project,
            profiles_dir=project,
            dbt_executable=executable,
            target=target_name,
        )
        result = transformer.run_transform(partition_key=partition, parameters=parameters)
        assert result.status == "success", result.error
        target = project / "target" / "runs" / run / "my_first_dbt_model"
        results = json.loads((target / "run_results.json").read_text())
        manifest = json.loads((target / "manifest.json").read_text())
        assert results["metadata"]["invocation_id"] == manifest["metadata"]["invocation_id"]
        snapshots.append((target, (target / "run_results.json").read_bytes()))
        with duckdb.connect(str(project / "test.duckdb")) as conn:
            value = conn.execute(f'select id from "{schema}".my_first_dbt_model').fetchone()[0]
        return target, value

    cold, value = build("cold")
    assert value == 3
    assert not (cold / "catalog.json").exists()
    assert "Unable to do partial parsing" in (cold / "logs" / "dbt.log").read_text()
    warm, value = build("warm")
    assert value == 3
    assert "Partial parsing enabled, no changes found" in (warm / "logs" / "dbt.log").read_text()
    changed_vars, value = build("vars", partition="7")
    assert value == 7
    assert "Unable to do partial parsing" in (changed_vars / "logs" / "dbt.log").read_text()
    model.write_text("select 11 as id")
    _, value = build("model")
    assert value == 11
    profiles = yaml.safe_load((project / "profiles.yml").read_text())
    profiles["test_profile"]["outputs"]["dev"]["schema"] = "changed"
    profiles["test_profile"]["outputs"]["wap"] = {
        **profiles["test_profile"]["outputs"]["dev"],
        "schema": "staged",
    }
    (project / "profiles.yml").write_text(yaml.safe_dump(profiles))
    model.write_text("select 17 as id")
    changed_profile, value = build("profile", schema="changed", generate_docs=True)
    assert value == 17
    assert "Unable to do partial parsing" in (changed_profile / "logs" / "dbt.log").read_text()
    assert (changed_profile / "catalog.json").exists()
    changed_target, value = build("target", target_name="wap", schema="staged")
    assert value == 17
    assert "Unable to do partial parsing" in (changed_target / "logs" / "dbt.log").read_text()
    for target, snapshot in snapshots:
        assert (target / "run_results.json").read_bytes() == snapshot
