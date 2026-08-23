"""CLI commands for querying ClickStack's bundled ClickHouse service.

This module provides Click CLI commands for executing SQL queries against
the running ClickStack ClickHouse container. It includes utilities for
validating container backend availability, reading SQL from files or inline arguments,
and executing queries with configurable formatting.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.commands.services import utils as services_utils
from phlo.cli.commands.services.utils import ensure_compose_project
from phlo.cli.infrastructure.command import CommandError, run_command
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import (
    empty_file_error,
    exclusive_options_error,
    file_read_error,
    missing_query_error,
)
from phlo.cli.sql import is_mutating_sql
from phlo.logging import get_logger
from phlo_clickstack.authorization import get_adapter as get_clickstack_adapter

logger = get_logger(__name__)


def _read_query(*, query: str | None, file: Path | None) -> str:
    """Read and validate SQL query from inline argument or file.

    Accepts either an inline SQL query string or a path to a SQL file;
    exactly one non-empty source must be provided. Raises ClickException
    when both are given, the file is unreadable/empty, or neither is
    provided.
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
    raise missing_query_error(command_hint='phlo clickstack query "SELECT 1"')


def _ensure_phlo_dir() -> Path:
    """Locate and return the local .phlo directory.

    Required for Docker Compose project configuration; raises
    ClickException when the directory does not exist.
    """
    return ensure_compose_project()


def _require_container_backend() -> None:
    """Validate that the selected container backend is available."""
    services_utils.require_container_backend()


@click.group(name="clickstack")
def clickstack_group() -> None:
    """Query and inspect the ClickStack service.

    Provides commands for interacting with the ClickStack ClickHouse
    container, including executing SQL queries.
    """


@clickstack_group.command(name="query")
@click.argument("query", required=False)
@click.option("--file", "query_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--format", "output_format", default="TabSeparatedRaw", show_default=True)
@click.option("--timeout", "timeout_seconds", default=30, show_default=True, type=int)
def clickstack_query(
    query: str | None,
    query_file: Path | None,
    output_format: str,
    timeout_seconds: int,
) -> None:
    """Execute a ClickHouse query against the running ClickStack service.

    Runs a SQL query via clickhouse-client inside the ClickStack container.
    Accepts SQL either as a command argument or from a file. Requires Docker
    and a running phlo services stack with ClickStack enabled; raises
    ClickException when Docker is unavailable, the .phlo directory is
    missing, inputs are invalid, execution fails, or the query times out.
    """
    sql = _read_query(query=query, file=query_file)
    if is_mutating_sql(sql):
        enforce_surface_mutation_authorization("clickstack.query", get_clickstack_adapter)
    _require_container_backend()
    phlo_dir = _ensure_phlo_dir()
    project_name = get_project_name()

    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name)
    cmd.extend(
        [
            "exec",
            "-T",
            "clickstack",
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
        raise click.ClickException(stderr or str(exc)) from exc
    except TimeoutExpired as exc:
        raise click.ClickException(f"Query timed out after {timeout_seconds} seconds.") from exc

    if result.stdout:
        click.echo(result.stdout, nl=False)
