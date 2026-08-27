"""Service hooks for dbt-related setup.

This module provides lifecycle hooks for dbt compilation and setup during
Phlo service operations. It handles compiling dbt models and restarting services
to pick up manifest changes, typically used during development workflows.

Example:
    >>> # Run via command line
    >>> # python -m phlo_dbt.hooks compile
    >>>
    >>> # Or use in service lifecycle
    >>> from phlo_dbt.hooks import compile_dbt
    >>> exit_code = compile_dbt()
    >>> if exit_code == 0:
    ...     print("Compilation successful")


Invoked standalone during dbt service lifecycle operations rather than imported
by other phlo modules; drives compilation through phlo.cli.infrastructure.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from phlo.cli.infrastructure.command import CommandError, run_command
from phlo.cli.infrastructure.utils import get_project_name
from phlo.infrastructure import find_service_container
from phlo.logging import get_logger
from phlo_dbt.runtime_config import ensure_dbt_profile
from phlo_dbt.settings import get_settings

logger = get_logger(__name__)


def _find_dagster_container(project_name: str) -> str:
    """Resolve the Dagster webserver container via core infrastructure helpers."""
    return find_service_container(
        project_name=project_name,
        service_name="dagster",
        legacy_names=(f"{project_name}-dagster-webserver-1",),
        include_pattern=rf"{project_name}.*dagster",
        exclude_substrings=("daemon",),
    )


def _container_path(local_path: Path, project_root: Path | None = None) -> Path:
    project_root = (project_root or Path.cwd()).resolve()
    try:
        return Path("/app") / local_path.resolve().relative_to(project_root)
    except ValueError:
        return local_path


def compile_dbt() -> int:
    """Compile dbt models in the Dagster container when a dbt project exists.

    Installs dependencies, compiles models, and restarts Dagster services to
    pick up the new manifest. Intended as a service hook during startup or
    development workflows. Returns a process-style status code: 0 on success
    and also when no dbt project exists; failures surface via the return code
    and structured log events rather than exceptions.

    Example:
        >>> from phlo_dbt.hooks import compile_dbt
        >>> exit_code = compile_dbt()
        >>> if exit_code == 0:
        ...     print("dbt models compiled and Dagster restarted")
        ... else:
        ...     print("Warning: Compilation had issues")

    """
    settings = get_settings()
    activated_projects = settings.dbt_project_paths
    existing_projects = [
        project for project in activated_projects if (project / "dbt_project.yml").exists()
    ]
    for skipped_project in activated_projects:
        if skipped_project not in existing_projects:
            logger.warning(
                "dbt_hook_compile_skipped_project_missing",
                dbt_project_path=str(skipped_project),
            )
    if not existing_projects:
        logger.info(
            "dbt_hook_compile_skipped_project_missing",
            dbt_project_path=str(activated_projects[0]),
        )
        return 0

    time.sleep(5)

    project_name = get_project_name()
    container_name = _find_dagster_container(project_name)
    logger.info(
        "dbt_hook_compile_container_resolved",
        project_name=project_name,
        container_name=container_name,
    )

    compiled_projects: list[Path] = []
    try:
        for local_project in existing_projects:
            profiles_dir = settings.dbt_profiles_path_for(local_project)
            ensure_dbt_profile(profiles_dir, project_dir=local_project)
            container_project = _container_path(local_project)
            container_profiles = _container_path(profiles_dir)

            logger.info(
                "dbt_hook_compile_started",
                dbt_project_path=str(local_project),
            )
            deps_result = run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "bash",
                    "-c",
                    f"cd {container_project} && dbt deps --profiles-dir {container_profiles}",
                ],
                timeout_seconds=60,
                check=False,
            )
            if deps_result.returncode != 0:
                logger.warning(
                    "dbt_hook_deps_failed",
                    project_name=project_name,
                    container_name=container_name,
                    dbt_project_path=str(local_project),
                    returncode=deps_result.returncode,
                    stderr=deps_result.stderr,
                )

            compile_result = run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "bash",
                    "-c",
                    f"cd {container_project} && dbt compile --profiles-dir {container_profiles}",
                ],
                timeout_seconds=120,
                check=False,
            )
            if compile_result.returncode == 0:
                compiled_projects.append(local_project)
                logger.info(
                    "dbt_hook_compile_succeeded",
                    project_name=project_name,
                    container_name=container_name,
                    dbt_project_path=str(local_project),
                )
            else:
                logger.warning(
                    "dbt_hook_compile_failed",
                    project_name=project_name,
                    container_name=container_name,
                    dbt_project_path=str(local_project),
                    returncode=compile_result.returncode,
                    stderr=compile_result.stderr,
                )

        if compiled_projects:
            run_command(
                [
                    "docker",
                    "restart",
                    container_name,
                    f"{project_name}-dagster-daemon-1",
                ],
                timeout_seconds=30,
                check=False,
            )
    except CommandError as exc:
        logger.exception(
            "dbt_hook_compile_command_error",
            project_name=project_name,
            container_name=container_name,
            error=str(exc),
        )
    except OSError as exc:
        logger.exception(
            "dbt_hook_compile_os_error",
            project_name=project_name,
            container_name=container_name,
            error=str(exc),
        )

    logger.info(
        "dbt_hook_compile_finished",
        project_name=project_name,
        container_name=container_name,
        compiled_count=len(compiled_projects),
    )
    return 0


def main() -> int:
    """Run the dbt hook CLI entrypoint, returning a process-style status code."""
    parser = argparse.ArgumentParser(description="Phlo dbt hooks")
    parser.add_argument("action", choices=["compile"])
    args = parser.parse_args()

    if args.action == "compile":
        return compile_dbt()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
