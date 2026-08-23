"""Integration tests for phlo-dbt.

Runs dbt against a temporary project to exercise CLI execution, direct
DbtTransformer execution, and project parsing. Skips cleanly when the duckdb
adapter is missing from the environment.
"""

import os
import shutil
import subprocess
from pathlib import Path

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


def test_dbt_cli_execution(dbt_project_dir):
    """Test that dbt CLI can execute in the project directory."""
    project_dir, dbt_exe = dbt_project_dir

    # 'dbt clean' is usually safe and doesn't require db connection
    result = subprocess.run(
        [dbt_exe, "clean"], cwd=project_dir, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, f"dbt clean failed: {result.stderr}"
    assert "Cleaned" in result.stdout or "clean" in result.stdout.lower()


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
    result = transformer.run_transform(
        partition_key=None,
        parameters={"generate_docs": False},  # Skip docs for speed
    )

    # Verify result
    assert result.status == "success", f"DbtTransformer failed: {result.error}"
    assert "dbt_output" in result.metadata


def test_dbt_parse_project(dbt_project_dir):
    """Test that dbt can parse the project."""
    project_dir, dbt_exe = dbt_project_dir

    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(project_dir)
    _skip_if_duckdb_adapter_missing(dbt_exe, project_dir, env)

    result = subprocess.run(
        [dbt_exe, "parse"], cwd=project_dir, env=env, capture_output=True, text=True, check=False
    )

    if result.returncode != 0 and _is_missing_duckdb_adapter(
        "\n".join((result.stdout, result.stderr))
    ):
        pytest.skip("dbt adapter not found, skipping parse test")

    if result.returncode == 0:
        assert (
            "dbt" in result.stdout
            or "Running with dbt" in result.stdout
            or "Done." in result.stdout
        )
    else:
        pytest.fail(
            f"dbt parse failed with code {result.returncode}.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
