"""Run the pack's dbt project against the local DuckDB build.

The dbt project is the same one the orchestrator runs. Only the profile
differs: ``profiles/profiles.yml`` points dbt at the DuckDB file the ingestion
step just wrote, while the orchestrator generates a Trino profile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from source_to_report import paths


def run(
    db_path: Path,
    *,
    quiet: bool = False,
    log: Callable[[str], None] = print,
    select: str | None = None,
    exclude: str | None = None,
) -> None:
    """Run ``dbt build`` (models and tests) against ``db_path``."""
    dbt_dir = paths.dbt_dir()
    profiles_dir = paths.local_dbt_profiles_dir()
    executable = shutil.which("dbt")
    if executable is None:
        raise RuntimeError(
            "dbt is not on PATH. Run the preset through `uv run` so it uses the project environment."
        )

    env = {
        **os.environ,
        "SOURCE_TO_REPORT_DUCKDB": str(db_path),
        "DBT_PROFILES_DIR": str(profiles_dir),
    }
    command = [
        executable,
        "build",
        "--project-dir",
        str(dbt_dir),
        "--profiles-dir",
        str(profiles_dir),
        "--target",
        "duckdb_local",
    ]
    if select:
        command.extend(["--select", select])
    if exclude:
        command.extend(["--exclude", exclude])
    if quiet:
        command.append("--quiet")

    log("  dbt     build (models and tests)")
    result = subprocess.run(command, env=env, cwd=paths.project_dir(), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"dbt build failed with exit code {result.returncode}")
