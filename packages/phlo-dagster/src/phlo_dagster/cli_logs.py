"""Logs command for accessing Dagster run logs.

Implements the ``phlo logs`` CLI command, querying Dagster's GraphQL API for
structured run events. Supports filtering by asset, job, level, time range,
and run ID; tail mode via --follow; Rich table or JSON output; human-readable
time windows (1h, 30m, 2d); and message truncation control with --full.

Example:
    CLI usage::

        phlo logs                           # Recent logs (last 100)
        phlo logs --asset dlt_orders        # Filter by asset
        phlo logs --job orders_pipeline     # Filter by job
        phlo logs --level ERROR             # Errors only
        phlo logs --since 1h                # Last hour
        phlo logs --follow                  # Tail mode
        phlo logs --run-id abc123           # Specific run
        phlo logs --full                    # Don't truncate messages

"""

import re
from datetime import datetime, timedelta, timezone

import click
from rich.console import Console

from phlo.logging import get_logger
from phlo_dagster.cli_logs_display import _display_logs, _tail_logs
from phlo_dagster.logs_client import _get_logs

console = Console()
logger = get_logger(__name__)


@click.command(help="Access and filter Dagster run logs.")
@click.option(
    "--asset",
    type=str,
    help="Filter by asset name",
)
@click.option(
    "--job",
    type=str,
    help="Filter by job name",
)
@click.option(
    "--level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    help="Filter by log level",
)
@click.option(
    "--since",
    type=str,
    help="Filter by time (e.g., 1h, 30m, 2d)",
)
@click.option(
    "--run-id",
    type=str,
    help="Get logs for specific run",
)
@click.option(
    "--follow",
    is_flag=True,
    default=False,
    help="Tail mode - follow new logs in real-time",
)
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Don't truncate long messages",
)
@click.option(
    "--limit",
    type=int,
    default=100,
    help="Number of logs to retrieve (default: 100)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="JSON output for scripting",
)
def logs(
    asset: str | None,
    job: str | None,
    level: str | None,
    since: str | None,
    run_id: str | None,
    follow: bool,
    full: bool,
    limit: int,
    output_json: bool,
):
    """Access and filter Dagster run logs per the CLI options above.

    Raises ClickException when incompatible options are provided.
    """
    if follow and output_json:
        raise click.ClickException(
            "--json cannot be combined with --follow yet. Use --json without --follow, "
            "or omit --json for live logs."
        )

    if not output_json:
        console.print("\n[bold blue]📋 Logs[/bold blue]\n")

    # Parse time filter
    start_time = _parse_since(since) if since else None
    logger.info(
        "dagster_logs_command_started",
        has_asset_filter=asset is not None,
        has_job_filter=job is not None,
        level=level,
        since=since,
        run_id=run_id,
        follow=follow,
        full=full,
        limit=limit,
        output_json=output_json,
    )

    # Build filters
    filters = {
        "asset": asset,
        "job": job,
        "level": level,
        "run_id": run_id,
        "start_time": start_time,
        "limit": limit,
    }

    if follow:
        _tail_logs(filters, full, output_json)
        logger.info(
            "dagster_logs_follow_mode_completed",
            limit=limit,
        )
    else:
        logs_data = _get_logs(filters)
        _display_logs(logs_data, full=full, output_json=output_json)
        logger.info(
            "dagster_logs_query_completed",
            log_count=len(logs_data),
            limit=limit,
        )


def _parse_since(since_str: str) -> datetime:
    """
    Parse a relative time filter string (e.g., '1h', '30m', '2d') into a UTC
    cutoff datetime; falls back to the last 24 hours on invalid input.
    """
    try:
        # Extract numeric part and unit
        match = re.match(r"(\d+)\s*([hmd])", since_str.lower())
        if not match:
            raise ValueError(f"Invalid time format: {since_str}")

        amount = int(match.group(1))
        unit = match.group(2)

        now = datetime.now(timezone.utc)
        if unit == "h":
            return now - timedelta(hours=amount)
        elif unit == "m":
            return now - timedelta(minutes=amount)
        elif unit == "d":
            return now - timedelta(days=amount)
        else:
            raise ValueError(f"Unknown time unit: {unit}")
    except Exception as e:
        logger.warning(
            "dagster_logs_since_parse_failed",
            since=since_str,
            error=str(e),
        )
        console.print("[yellow]Invalid --since value; defaulting to last 24 hours[/yellow]")
        return datetime.now(timezone.utc) - timedelta(hours=24)  # Default to last 24 hours
