"""CLI commands for the Trino query engine service.

This module provides CLI commands for interacting with Trino, including:
    - Running the Trino interactive shell
    - Executing SQL queries from command line or files
    - Managing query output formats and timeouts

Functions:
    trino_group: Main CLI command group for Trino operations.
    trino_query: Execute SQL queries against Trino.
    _read_query: Read SQL from inline argument or file.
    _require_container_backend: Validate container backend CLI availability.
    _trino_exec_base: Build docker compose exec command.

Example:
    $ phlo trino
    $ phlo trino query "SELECT 1"
    $ phlo trino query --file query.sql --catalog iceberg --schema my_schema

"""

from __future__ import annotations

import subprocess
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
)
from phlo.cli.output import missing_query_error
from phlo.cli.sql import is_mutating_sql
from phlo.plugins.observatory_settings import StorageUnavailableError, get_operational_settings
from phlo_trino.authorization import get_trino_cli_adapter
from phlo_trino.sql_policy import require_governed_sql


def _read_query(*, query: str | None, file: Path | None) -> str:
    """Return SQL text from inline query or file input."""
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
    raise missing_query_error(command_hint='phlo trino query "SELECT 1"')


def _require_container_backend() -> None:
    """Validate that the selected container backend is available."""
    _require_selected_container_backend()


def _trino_exec_base(*, tty: bool) -> list[str]:
    """Build the docker compose exec command for the Trino container."""
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()
    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name)
    cmd.append("exec")
    if not tty:
        cmd.append("-T")
    cmd.extend(["trino", "trino"])
    return cmd


@click.command(
    name="trino",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("trino_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def trino_group(ctx: click.Context, trino_args: tuple[str, ...]) -> None:
    """Run the Trino shell or a Trino-specific helper command."""
    if trino_args and trino_args[0] == "query":
        trino_query.main(
            args=list(trino_args[1:]),
            prog_name="phlo trino query",
            standalone_mode=False,
        )
        return
    if get_operational_settings().protect_main:
        raise click.ClickException(
            "The unrestricted Trino shell cannot enforce main protection. "
            "Use phlo trino query for reads or a branch-scoped provider for writes."
        )
    _require_container_backend()
    enforce_surface_mutation_authorization("trino", get_trino_cli_adapter)
    cmd = _trino_exec_base(tty=True)
    cmd.extend(trino_args)
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise click.ClickException(f"Trino shell exited with status {result.returncode}.")


@click.command(name="query")
@click.argument("query", required=False)
@click.option(
    "--file",
    "query_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--catalog", default=None, help="Catalog name for the query session.")
@click.option("--schema", "schema_name", default=None, help="Schema name for the query session.")
@click.option("--output-format", default="CSV_HEADER_UNQUOTED", show_default=True)
@click.option("--timeout", "timeout_seconds", default=30, show_default=True, type=int)
def trino_query(
    query: str | None,
    query_file: Path | None,
    catalog: str | None,
    schema_name: str | None,
    output_format: str,
    timeout_seconds: int,
) -> None:
    """Execute a SQL query against the running Trino service."""
    sql = _read_query(query=query, file=query_file)
    if is_mutating_sql(sql):
        enforce_surface_mutation_authorization("trino.query", get_trino_cli_adapter)
    try:
        # A user-selected catalog is not proof of its backing Nessie ref.
        require_governed_sql(sql, ref=None, catalog=catalog)
    except (PermissionError, StorageUnavailableError) as exc:
        raise click.ClickException(str(exc)) from exc
    _require_container_backend()
    cmd = _trino_exec_base(tty=False)
    if catalog:
        cmd.extend(["--catalog", catalog])
    if schema_name:
        cmd.extend(["--schema", schema_name])
    cmd.extend(["--output-format", output_format, "--execute", sql])

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
            "trino",
            exit_code=exc.returncode,
            details=[stderr] if stderr else ["The Trino service did not complete the query."],
            run="phlo services status trino",
        ) from exc
    except TimeoutExpired as exc:
        raise click.ClickException(f"Query timed out after {timeout_seconds} seconds.") from exc

    if result.stdout:
        click.echo(result.stdout, nl=False)
