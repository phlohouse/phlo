"""CLI commands for Sling replication management.

This module implements the Click command-line interface for Sling replication
operations. It provides commands for running ad-hoc replications, listing
available connections, and discovering source streams from connections.

Commands:
    sling_group: Main command group for Sling operations.
    run_command: Execute a Sling replication.
    conns_command: List available Sling connections.
    discover_command: Discover available streams from a connection.

Functions:
    _resolve_target_object: Resolve the destination object for ad-hoc runs.
    _get_sling_binary: Return the Sling binary path.
    _run_sling_cli_command: Execute the Sling CLI and capture output.
    _parse_discovery_output: Parse Sling's ASCII table into JSON.
    _normalize_column_name: Normalize table headers for JSON output.
"""

from __future__ import annotations

import json
import subprocess

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.output import command_failed_error
from phlo.logging import get_logger
from phlo_sling.authorization import get_adapter as get_sling_adapter
from phlo_sling.connections import apply_sling_connection_env
from phlo_sling.settings import get_settings

logger = get_logger(__name__)


@click.group("sling")
def sling_group() -> None:
    """Sling replication commands.

    This command group provides operations for managing Sling-based data
    replications within the Phlo platform. Commands include running
    replications, listing connections, and discovering source streams.
    """


@sling_group.command("run")
@click.option("--replication", "-r", type=click.Path(exists=True), help="Sling replication YAML.")
@click.option("--source", "-s", help="Source connection name.")
@click.option("--target", "-t", help="Target connection name.")
@click.option("--stream", help="Source stream (e.g., 'public.users').")
@click.option("--object", "target_object", help="Target object/table name.")
@click.option(
    "--mode",
    default=None,
    help="Replication mode. Defaults to SLING_DEFAULT_MODE when omitted.",
)
def run_command(
    replication: str | None,
    source: str | None,
    target: str | None,
    stream: str | None,
    target_object: str | None,
    mode: str | None,
) -> None:
    """Run a Sling replication.

    Execute a data replication using Sling. Either provide a replication
    YAML configuration file or specify source/target/stream parameters
    for ad-hoc execution.

    Raises: click.UsageError if neither replication file nor source/stream parameters are provided.
    """
    from sling import Replication, Sling

    enforce_surface_mutation_authorization("sling.run", get_sling_adapter)
    apply_sling_connection_env()
    resolved_mode = mode or get_settings().sling_default_mode

    if replication:
        click.echo(f"Running replication from {replication}")
        repl = Replication(file_path=replication)
        repl.run()
    elif source and stream:
        if not target:
            raise click.UsageError("Provide --target for ad-hoc runs.")
        resolved_target_object = _resolve_target_object(stream=stream, target_object=target_object)
        click.echo(f"Replicating {stream} from {source}")
        config = Sling(
            src_conn=source,
            src_stream=stream,
            tgt_conn=target,
            tgt_object=resolved_target_object,
            mode=resolved_mode,
        )
        config.run()
    else:
        raise click.UsageError("Provide --replication YAML or --source/--stream.")


@sling_group.command("conns")
@click.option("--auto/--no-auto", default=True, help="Include auto-discovered connections.")
def conns_command(auto: bool) -> None:
    """List available Sling connections.

    Shows auto-discovered connections from Phlo capability metadata and any
    connections from explicit env.yaml files. This helps verify that Phlo
    packages are properly configured and connections are available.
    """
    if auto:
        from phlo_sling.connections import resolve_phlo_connections

        connections = resolve_phlo_connections()
        if connections:
            click.echo("Auto-discovered connections:")
            for name, config in connections.items():
                conn_type = config.get("type", "unknown")
                host = config.get("host") or config.get("endpoint", "")
                click.echo(f"  {name}: {conn_type} ({host})")
        else:
            click.echo("No auto-discovered connections found.")

    click.echo("\nSling native connections:")
    try:
        result = _run_sling_cli_command(["conns", "list"])
        click.echo(result.stdout, nl=False)
    except Exception as exc:
        logger.warning("sling_native_connections_list_failed", error=str(exc), exc_info=True)
        click.echo("  Native Sling connections unavailable. Run: sling conns list")


@sling_group.command("discover")
@click.argument("connection")
@click.option("--schema", help="Filter by schema name.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def discover_command(connection: str, schema: str | None, output_format: str) -> None:
    """Discover available streams from a Sling connection.

    Lists tables/views available in the source connection for use as
    stream_name in @phlo_sling_replication decorators. This is useful
    for exploring source databases before defining replications.

    Raises: click.ClickException if discovery fails.
    """
    apply_sling_connection_env()

    try:
        command = ["conns", "discover", connection]
        if schema:
            command.extend(["--pattern", f"{schema}.*"])

        result = _run_sling_cli_command(command)
        if output_format == "json":
            click.echo(json.dumps(_parse_discovery_output(result.stdout), indent=2))
            return

        click.echo(f"Streams from {connection}:")
        click.echo(result.stdout, nl=False)
    except Exception as exc:
        logger.error(
            "sling_discovery_failed",
            connection=connection,
            schema=schema,
            output_format=output_format,
            error=str(exc),
            exc_info=True,
        )
        raise command_failed_error(
            "Sling discovery",
            details=[f"Connection: {connection}"],
            run="phlo sling conns",
        ) from exc


def _resolve_target_object(stream: str, target_object: str | None) -> str:
    """Resolve the destination object for an ad-hoc Sling run.

    Determines the target object name from the stream or explicit parameter.
    Rejects wildcards without explicit target specification.

    Raises: click.UsageError if stream contains wildcard without explicit target.
    """
    if target_object:
        return target_object
    if "*" in stream:
        raise click.UsageError("Provide --object when --stream uses a wildcard.")
    return stream


def _get_sling_binary() -> str:
    """Return the Sling binary path, honoring package settings.

    Checks for an override in settings first, then falls back to the
    bundled binary from the sling package.

    Raises: ImportError if sling package is not installed.
    """
    settings = get_settings()
    if settings.sling_binary_path:
        return settings.sling_binary_path

    from sling.bin import SLING_BIN

    return SLING_BIN


def _run_sling_cli_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute the Sling CLI and return captured output.

    Runs a Sling CLI command with the specified arguments and captures
    stdout/stderr.

    Raises: subprocess.CalledProcessError if the command exits non-zero.
    """
    return subprocess.run(
        [_get_sling_binary(), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _parse_discovery_output(output: str) -> list[dict[str, str]]:
    """Parse Sling's ASCII discovery table into JSON-serializable rows.

    Converts the ASCII table output from Sling's discover command into
    a list of dictionaries suitable for JSON serialization.
    """
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    table_lines = [line for line in lines if "|" in line]
    if len(table_lines) < 2:
        return []

    headers = [_normalize_column_name(part) for part in table_lines[0].split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[1:]:
        values = [part.strip() for part in line.split("|")]
        if len(values) != len(headers):
            continue
        # Skip the ASCII table's dash-only separator rows.
        if all(set(value) <= {"-"} for value in values):
            continue
        rows.append(dict(zip(headers, values, strict=True)))

    return rows


def _normalize_column_name(value: str) -> str:
    """Normalize discovery table headers for JSON output.

    Converts column headers from Sling's table format to snake_case
    suitable for JSON keys.
    """
    return value.strip().lower().replace(" ", "_")
