"""CLI commands for the ClickHouse data plane service.

This module provides command-line interface commands for interacting with
ClickHouse services, including SQL query execution and status monitoring.

Example:
    Using the CLI commands:

    $ phlo clickhouse query "SELECT version()"
    24.3.0

    $ phlo clickhouse status
    version: 24.3.0
    uptime_seconds: 3600
    current_database: default

"""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.commands.services.utils import (
    ensure_compose_project,
    require_container_backend as _require_selected_container_backend,
)
from phlo.cli.infrastructure.command import CommandError, run_command
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import (
    command_failed_error,
    empty_file_error,
    exclusive_options_error,
    file_read_error,
    missing_query_error,
)
from phlo.cli.sql import is_mutating_sql
from phlo.logging import get_logger
from phlo_clickhouse.authorization import get_adapter as get_clickhouse_adapter

logger = get_logger(__name__)


def _read_query(*, query: str | None, file: Path | None) -> str:
    """Read and validate SQL from an inline string or a file; exactly one source
    must be provided and the content must be non-empty.

    Returns whitespace-stripped SQL text.

    Raises: click.ClickException when both or neither source is given, the
    file cannot be read, or the file is empty.

    Example:
        >>> from pathlib import Path
        >>> _read_query(query="SELECT 1", file=None)
        'SELECT 1'
    """
    if query and file:
        raise exclusive_options_error("an inline query", "--file")
    if file is not None:
        try:
            sql = file.read_text(encoding="utf-8")
        except OSError as exc:
            raise file_read_error(file) from exc
        if sql.strip():
            return sql
        raise empty_file_error(file)
    if query and query.strip():
        return query
    raise missing_query_error(command_hint='phlo clickhouse query "SELECT 1"')


def _ensure_phlo_dir() -> Path:
    """Verify and return the Phlo compose project directory.

    Raises: click.ClickException when the compose project files are missing.

    Example:
        >>> # In a directory with .phlo/ subdirectory
        >>> path = _ensure_phlo_dir()
        >>> path.name
        '.phlo'
    """
    return ensure_compose_project()


def _require_container_backend() -> None:
    """Validate that the selected container backend is available."""
    _require_selected_container_backend()


@click.group(name="clickhouse")
def clickhouse_group() -> None:
    """Query and inspect the ClickHouse data plane service.

    This command group provides tools for interacting with ClickHouse,
    including SQL query execution and service status monitoring.

    Example:
        $ phlo clickhouse --help
        $ phlo clickhouse query "SELECT 1"

    """


@clickhouse_group.command(name="query")
@click.argument("query", required=False)
@click.option(
    "--file",
    "query_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to SQL file containing the query to execute.",
)
@click.option(
    "--format",
    "output_format",
    default="TabSeparatedRaw",
    show_default=True,
    help="Output format for query results (e.g., TabSeparatedRaw, JSON, CSV).",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    default=30,
    show_default=True,
    type=int,
    help="Query execution timeout in seconds.",
)
def clickhouse_query(
    query: str | None,
    query_file: Path | None,
    output_format: str,
    timeout_seconds: int,
) -> None:
    """Execute a SQL query against the running ClickHouse service via
    clickhouse-client inside the container backend, printing results to stdout.

    Raises: click.ClickException when the container backend is unavailable,
    the query fails, or execution times out.

    Example:
        $ phlo clickhouse query "SELECT version()"
        $ phlo clickhouse query --file queries/analysis.sql --format CSV
    """
    sql = _read_query(query=query, file=query_file)
    if is_mutating_sql(sql):
        enforce_surface_mutation_authorization("clickhouse.query", get_clickhouse_adapter)
    _require_container_backend()
    phlo_dir = _ensure_phlo_dir()
    project_name = get_project_name()

    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name)
    cmd.extend(
        [
            "exec",
            "-T",
            "clickhouse",
            "clickhouse-client",
            "--multiquery",
            "--format",
            output_format,
            "--query",
            sql,
        ]
    )

    try:
        result = run_command(
            cmd,
            timeout_seconds=timeout_seconds,
            capture_output=True,
            check=True,
        )
    except CommandError as exc:
        stderr = exc.stderr.strip()
        raise command_failed_error(
            "clickhouse-client",
            exit_code=exc.returncode,
            details=[f"ClickHouse error: {stderr}"] if stderr else None,
            run='phlo clickhouse query "SELECT 1"',
        ) from exc
    except TimeoutExpired as exc:
        raise click.ClickException(f"Query timed out after {timeout_seconds} seconds.") from exc

    if result.stdout:
        click.echo(result.stdout, nl=False)


@clickhouse_group.command(name="status")
def clickhouse_status() -> None:
    """Show ClickHouse service status: version, uptime, and current database.

    Raises: click.ClickException when the container backend is unavailable or
    the status check fails.

    Example:
        $ phlo clickhouse status
        version    uptime_seconds    current_database
        24.3.0     3600              default
    """
    _require_container_backend()
    phlo_dir = _ensure_phlo_dir()
    project_name = get_project_name()

    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name)
    cmd.extend(
        [
            "exec",
            "-T",
            "clickhouse",
            "clickhouse-client",
            "--query",
            "SELECT version() AS version, uptime() AS uptime_seconds, "
            "currentDatabase() AS current_database",
        ]
    )

    try:
        result = run_command(
            cmd,
            timeout_seconds=10,
            capture_output=True,
            check=True,
        )
    except CommandError as exc:
        raise command_failed_error(
            "clickhouse-client",
            exit_code=exc.returncode,
            details=["ClickHouse did not respond to the status query."],
            run="phlo services status clickhouse",
        ) from exc
    except TimeoutExpired as exc:
        raise click.ClickException("Status check timed out.") from exc

    if result.stdout:
        click.echo(result.stdout, nl=False)
