"""CLI plugin for dbt-related commands.

This module provides the dbt CLI command group for the Phlo CLI, enabling
dbt operations like compile, run, and test to be executed either locally or
within the orchestrator container. It also handles lineage import after successful runs.

Example:
    >>> # Via CLI:
    >>> # phlo dbt compile
    >>> # phlo dbt run --target prod --select mrt_orders
    >>> # phlo dbt test --select tag:orders
    >>>
    >>> # Programmatically:
    >>> from phlo_dbt.cli_plugin import DbtCliPlugin
    >>> plugin = DbtCliPlugin()
    >>> commands = plugin.get_cli_commands()

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Composes dbt commands from phlo.cli infrastructure/output helpers and phlo.plugins.base.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.commands.services.utils import ensure_phlo_dir
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import user_error
from phlo.logging import get_logger
from phlo.plugins.base import cli_command_plugin_class
from phlo_dbt.authorization import get_dbt_adapter
from phlo_dbt.cli_publishing import publishing
from phlo_dbt.runtime_config import DEFAULT_DBT_TARGET, ensure_dbt_profile

DBT_PROJECT_HELP = (
    "Create or copy a dbt project under workflows/, for example "
    "workflows/transforms/dbt or workflows/<name>/transforms/dbt, or set DBT_PROJECT_DIR."
)


def _container_path(path: Path, *, project_root: Path) -> str:
    """Translate a project-local host path into the orchestrator container mount path.

    Converts a local filesystem path to the corresponding path inside the
    Docker container where the project is mounted (typically under /app).

    Example:
        >>> from pathlib import Path
        >>> local = Path("workflows/transforms/dbt")
        >>> container = _container_path(local, project_root=Path("."))
        >>> print(container)
        /app/workflows/transforms/dbt
    """
    relative = path.resolve().relative_to(project_root.resolve())
    return str(Path("/app") / relative)


def _should_run_in_container(local: bool) -> bool:
    """Choose the default execution environment for dbt commands.

    Determines whether dbt commands should run in the orchestrator container
    or on the local host. Container execution is preferred when a Phlo
    project directory exists.

    Example:
        >>> # In a project with .phlo directory
        >>> should_container = _should_run_in_container(local=False)
        >>> print(should_container)
        True
        >>>
        >>> # Force local execution
        >>> should_local = _should_run_in_container(local=True)
        >>> print(should_local)
        False
    """
    if local:
        return False
    try:
        ensure_phlo_dir()
    except (SystemExit, click.ClickException):
        return False
    return True


def _resolve_exec_service_name() -> str:
    """Resolve the execution service from the active orchestrator adapter."""
    from phlo.orchestrators import get_active_orchestrator

    adapter = get_active_orchestrator()
    service_name = adapter.exec_service_name()
    if not service_name:
        raise click.ClickException(
            "The active orchestrator does not expose a container execution service. "
            "Use --local to run dbt on the host."
        )
    return service_name


def _import_manifest_lineage(manifest_path: Path) -> dict[str, int]:
    """Import dbt lineage lazily to avoid plugin discovery-time import cycles."""
    from phlo_dbt.lineage_import import import_manifest_lineage

    return import_manifest_lineage(manifest_path)


def _run_dbt_in_container(
    *,
    subcommand: str,
    target: str,
    select_expr: str | None = None,
) -> None:
    """Run dbt inside the active orchestrator service container."""
    from phlo_dbt.settings import get_settings

    logger = get_logger(f"phlo.dbt.{subcommand}")
    settings = get_settings()
    project_root = Path.cwd()
    project_dir = settings.dbt_project_path
    profiles_dir = settings.dbt_profiles_path
    exec_service_name = _resolve_exec_service_name()

    if not (project_dir / "dbt_project.yml").exists():
        raise user_error(
            "no dbt project found",
            missing=project_dir / "dbt_project.yml",
            details=[DBT_PROJECT_HELP],
            run="phlo workflow create --help",
        )

    phlo_dir = ensure_phlo_dir()
    compose_cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=get_project_name())
    cmd = [*compose_cmd, "exec", "-T", exec_service_name, "dbt", subcommand]
    cmd.extend(["--project-dir", _container_path(project_dir, project_root=project_root)])
    cmd.extend(["--profiles-dir", _container_path(profiles_dir, project_root=project_root)])
    cmd.extend(["--target", target])
    if select_expr is not None:
        cmd.extend(["--select", select_expr])

    click.echo(f"Running dbt {subcommand} in {exec_service_name}...")
    logger.debug(
        "dbt_command_container_started",
        subcommand=subcommand,
        project_dir=str(project_dir),
        service_name=exec_service_name,
        target=target,
        select=select_expr,
    )
    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode == 0:
            _import_lineage_after_run(subcommand=subcommand, project_dir=project_dir, logger=logger)
        sys.exit(result.returncode)
    except FileNotFoundError:
        click.echo("Error: docker command not found", err=True)
        sys.exit(1)


def _import_lineage_after_run(
    *,
    subcommand: str,
    project_dir: Path,
    logger: Any,
) -> None:
    """Import manifest lineage after a successful dbt CLI run."""
    if subcommand != "run":
        return

    manifest_path = project_dir / "target" / "manifest.json"
    try:
        summary = _import_manifest_lineage(manifest_path)
    except Exception:
        logger.warning(
            "dbt_cli_lineage_import_failed",
            manifest_path=str(manifest_path),
            exc_info=True,
        )
        return

    logger.info(
        "dbt_cli_lineage_import_succeeded",
        manifest_path=str(manifest_path),
        asset_edge_count=summary["asset_edges"],
        column_mapping_count=summary["column_mappings"],
    )


def _run_dbt_local(subcommand: str, target: str, select_expr: str | None = None) -> None:
    """Run a dbt subcommand against the local project."""
    from phlo_dbt.settings import get_settings

    logger = get_logger(f"phlo.dbt.{subcommand}")
    settings = get_settings()
    project_dir = settings.dbt_project_path
    profiles_dir = settings.dbt_profiles_path

    if not (project_dir / "dbt_project.yml").exists():
        raise user_error(
            "no dbt project found",
            missing=project_dir / "dbt_project.yml",
            details=[DBT_PROJECT_HELP],
            run="phlo workflow create --help",
        )

    ensure_dbt_profile(profiles_dir, target=target)

    cmd = [
        "dbt",
        subcommand,
        "--profiles-dir",
        str(profiles_dir),
        "--target",
        target,
    ]
    if select_expr is not None:
        cmd.extend(["--select", select_expr])

    click.echo(f"Running dbt {subcommand} at {project_dir}...")
    logger.debug(
        "dbt_command_started",
        subcommand=subcommand,
        project_dir=str(project_dir),
        target=target,
        select=select_expr,
    )
    try:
        result = subprocess.run(cmd, cwd=str(project_dir), check=False)
        if result.returncode == 0:
            _import_lineage_after_run(subcommand=subcommand, project_dir=project_dir, logger=logger)
        sys.exit(result.returncode)
    except FileNotFoundError:
        click.echo("Error: dbt command not found", err=True)
        sys.exit(1)


def _run_dbt(
    subcommand: str,
    target: str,
    select_expr: str | None = None,
    *,
    local: bool,
) -> None:
    """Run a dbt subcommand locally or inside the orchestrator container."""
    if _should_run_in_container(local):
        _run_dbt_in_container(subcommand=subcommand, target=target, select_expr=select_expr)
        return
    _run_dbt_local(subcommand, target, select_expr)


@click.group("dbt")
def dbt_group() -> None:
    """Dbt commands (compile, run, test, publishing)."""


@dbt_group.command("compile")
@click.option("--target", default=DEFAULT_DBT_TARGET, help="dbt target profile")
@click.option(
    "--local", is_flag=True, help="Run dbt on the host instead of in the orchestrator container."
)
def compile_cmd(target: str, local: bool) -> None:
    """Compile dbt models in the local project."""
    _run_dbt("compile", target, local=local)


@dbt_group.command("run")
@click.option("--target", default=DEFAULT_DBT_TARGET, help="dbt target profile")
@click.option("--select", "select_exprs", multiple=True, help="dbt model selector")
@click.option(
    "--local", is_flag=True, help="Run dbt on the host instead of in the orchestrator container."
)
def run_cmd(target: str, select_exprs: tuple[str, ...], local: bool) -> None:
    """Run dbt models in the local project."""
    enforce_surface_mutation_authorization("dbt.run", get_dbt_adapter)
    _run_dbt("run", target, " ".join(select_exprs) or None, local=local)


@dbt_group.command("test")
@click.option("--target", default=DEFAULT_DBT_TARGET, help="dbt target profile")
@click.option("--select", "select_exprs", multiple=True, help="dbt model selector")
@click.option(
    "--local", is_flag=True, help="Run dbt on the host instead of in the orchestrator container."
)
def test_cmd(target: str, select_exprs: tuple[str, ...], local: bool) -> None:
    """Run dbt tests in the local project."""
    _run_dbt("test", target, " ".join(select_exprs) or None, local=local)


dbt_group.add_command(publishing)


DbtCliPlugin = cli_command_plugin_class(
    "DbtCliPlugin",
    name="dbt",
    version="0.1.0",
    description="dbt CLI commands",
    commands=[dbt_group],
)
