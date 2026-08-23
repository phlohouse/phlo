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

import json
import re
from datetime import datetime, timedelta, timezone

import click
import psycopg2
import psycopg2.extras
import requests as http_requests
from rich.console import Console

from phlo.config.env import load_project_env
from phlo.config.network import resolve_host
from phlo.logging import get_logger
from phlo_dagster.cli_logs_display import _display_logs, _tail_logs
from phlo_dagster.settings import get_settings

console = Console()
logger = get_logger(__name__)


def _project_env() -> dict[str, str]:
    """Load project-level Phlo env files for host-side log lookups."""
    return load_project_env()


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


def _get_logs(filters: dict) -> list[dict]:
    """
    Retrieve filtered log dictionaries from Dagster, preferring the Postgres
    path when a level filter is set.
    """
    if filters.get("level"):
        postgres_logs = _get_logs_from_postgres(filters)
        if postgres_logs:
            logger.debug("dagster_logs_level_filter_postgres_used")
            return postgres_logs

    try:
        env = _project_env()
        settings = get_settings()
        dagster_host = env.get("DAGSTER_WEBSERVER_HOST", "localhost")
        dagster_port = (
            env.get("DAGSTER_WEBSERVER_PORT")
            or env.get("DAGSTER_PORT")
            or str(settings.dagster_port)
        )

        dagster_url = f"http://{dagster_host}:{dagster_port}/graphql"

        # Build GraphQL query
        query = _build_logs_query(filters)
        logger.debug(
            "dagster_logs_graphql_query_started",
            limit=filters.get("limit", 100),
            has_level_filter=filters.get("level") is not None,
            has_run_filter=filters.get("run_id") is not None,
        )

        try:
            response = http_requests.post(dagster_url, json={"query": query}, timeout=5)
            response.raise_for_status()
            result = response.json()

            logs_list: list[dict] = []

            if result and "data" in result:
                runs = result["data"].get("runsOrError", {}).get("results", [])
                for run in runs:
                    run_id = run.get("runId", "")
                    job_name = run.get("jobName", "")
                    run_status = run.get("status", "")

                    # Get events for this run
                    event_connection = run.get("eventConnection", {}) or {}
                    events = event_connection.get("events", [])
                    for event in events:
                        event_type = event.get("eventType") or event.get("__typename", "")
                        message = event.get("message", "")
                        timestamp = event.get("timestamp")
                        event_level = _get_log_level(event_type)

                        log_entry = {
                            "timestamp": timestamp,
                            "level": event_level,
                            "message": message,
                            "event_type": event_type,
                            "run_id": run_id,
                            "job_name": job_name,
                            "run_status": run_status,
                        }

                        # Apply level filter
                        if filters.get("level") and event_level != filters["level"]:
                            continue

                        logs_list.append(log_entry)

            logger.debug(
                "dagster_logs_graphql_query_completed",
                log_count=len(logs_list),
            )
            return logs_list

        except Exception:
            postgres_logs = _get_logs_from_postgres(filters)
            if postgres_logs:
                logger.debug("dagster_logs_graphql_failed_postgres_fallback_used")
                return postgres_logs
            logger.warning("dagster_logs_graphql_query_failed", exc_info=True)
            return []

    except Exception:
        logger.info(
            "dagster_logs_graphql_client_unavailable",
            exc_info=True,
        )
        postgres_logs = _get_logs_from_postgres(filters)
        if postgres_logs:
            logger.debug("dagster_logs_graphql_client_unavailable_postgres_fallback_used")
        return postgres_logs


def _get_logs_from_postgres(filters: dict) -> list[dict]:
    """Retrieve Dagster event logs directly from Dagster's Postgres storage."""
    try:
        env = _project_env()
        host, port = resolve_host(
            env.get("POSTGRES_HOST", "postgres"),
            int(env.get("POSTGRES_PORT", "5432")),
            port_env_var="POSTGRES_PORT",
        )
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=env.get("POSTGRES_DB", "phlo"),
            user=env.get("POSTGRES_USER", "phlo"),
            password=env.get("POSTGRES_PASSWORD", "phlo"),
        )
    except Exception:
        logger.warning("dagster_logs_postgres_connect_failed", exc_info=True)
        return []

    where = ["TRUE"]
    params: list[object] = []
    if filters.get("asset"):
        asset = str(filters["asset"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("asset_key ILIKE %s ESCAPE '\\'")
        params.append(f"%{asset}%")
    if filters.get("run_id"):
        where.append("run_id = %s")
        params.append(filters["run_id"])
    if filters.get("start_time"):
        where.append("timestamp >= %s")
        params.append(filters["start_time"])

    requested_limit = int(filters.get("limit", 100))
    query_limit = requested_limit
    # Level filtering happens after decoding rows in Python, so over-fetch
    # enough raw rows to still fill the requested limit post-filter.
    if filters.get("level"):
        query_limit = max(requested_limit * 20, 200)

    params.append(query_limit)
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"""
                SELECT run_id, dagster_event_type, timestamp, event, step_key
                FROM event_logs
                WHERE {" AND ".join(where)}
                ORDER BY id DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
    except Exception:
        logger.warning("dagster_logs_postgres_query_failed", exc_info=True)
        return []
    finally:
        conn.close()

    logs_list: list[dict] = []
    for row in rows:
        entry = _event_log_row_to_entry(row)
        if not entry:
            continue
        if filters.get("job") and entry.get("job_name") != filters["job"]:
            continue
        if filters.get("level") and entry.get("level") != filters["level"]:
            continue
        logs_list.append(entry)
        if len(logs_list) >= requested_limit:
            break
    logs_list.reverse()
    return logs_list


def _event_log_row_to_entry(row) -> dict | None:
    raw_event = row.get("event")
    payload: dict = {}
    if isinstance(raw_event, str) and raw_event:
        try:
            payload = json.loads(raw_event)
        except json.JSONDecodeError:
            payload = {}

    dagster_event = payload.get("dagster_event") or {}
    logging_tags = dagster_event.get("logging_tags") or {}
    event_type = row.get("dagster_event_type") or dagster_event.get("event_type_value") or ""
    error_message = _extract_error_message(dagster_event)
    message = (
        error_message
        or payload.get("user_message")
        or dagster_event.get("message")
        or payload.get("message")
        or event_type
    )
    timestamp = row.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp_value = timestamp.replace(tzinfo=timezone.utc).isoformat()
    else:
        timestamp_value = str(timestamp) if timestamp is not None else ""

    return {
        "timestamp": timestamp_value,
        "level": _level_from_event_payload(payload, event_type),
        "message": str(message or ""),
        "event_type": str(event_type),
        "run_id": str(row.get("run_id") or payload.get("run_id") or ""),
        "job_name": str(logging_tags.get("job_name") or payload.get("pipeline_name") or ""),
        "run_status": "",
    }


def _extract_error_message(dagster_event: dict) -> str | None:
    """Extract the most specific useful error message from a Dagster event payload."""
    event_data = dagster_event.get("event_specific_data")
    candidates: list[dict] = []
    if isinstance(event_data, dict):
        if isinstance(event_data.get("error"), dict):
            candidates.append(event_data["error"])
        first_failure = event_data.get("first_step_failure_event")
        if isinstance(first_failure, dict):
            first_failure_data = first_failure.get("event_specific_data")
            if isinstance(first_failure_data, dict) and isinstance(
                first_failure_data.get("error"), dict
            ):
                candidates.append(first_failure_data["error"])

    messages: list[str] = []
    for candidate in candidates:
        current: dict | None = candidate
        while isinstance(current, dict):
            message = current.get("message")
            if isinstance(message, str) and message.strip():
                messages.append(message.strip())
            next_cause = current.get("cause")
            current = next_cause if isinstance(next_cause, dict) else None

    if not messages:
        return None

    return _clean_error_message(messages[-1])


def _clean_error_message(message: str) -> str:
    """Return the first readable sentence from a nested framework error."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return message.strip()
    if lines[0].startswith(("dlt.", "dagster.")):
        _, _, detail = lines[0].partition(":")
        if detail.strip():
            return detail.strip()
        if len(lines) >= 2:
            return lines[1]
    return lines[0]


def _level_from_event_payload(payload: dict, event_type: str) -> str:
    level = payload.get("level")
    if isinstance(level, int):
        if level >= 40:
            return "ERROR"
        if level >= 30:
            return "WARNING"
        if level >= 20:
            return "INFO"
    return _get_log_level(event_type)


def _build_logs_query(filters: dict) -> str:
    """
    Build the GraphQL query used to fetch run log events.
    """
    # Simplified query structure - in production would be more comprehensive
    limit = int(filters.get("limit", 100))
    event_limit = max(limit, 1)
    if filters.get("level"):
        event_limit = max(event_limit * 20, 200)
    query = """
    {
        runsOrError(limit: %d) {
            ... on Runs {
                results {
                    runId
                    jobName
                    status
                    startTime
                    endTime
                    eventConnection(limit: %d) {
                        events {
                            __typename
                            ... on ExecutionStepInputEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on ExecutionStepOutputEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on ExecutionStepFailureEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on ExecutionStepSuccessEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on RunStartEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on RunSuccessEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on RunFailureEvent {
                                eventType
                                message
                                timestamp
                            }
                            ... on LogMessageEvent {
                                eventType
                                message
                                timestamp
                                level
                            }
                        }
                    }
                }
            }
        }
    }
    """ % (limit, event_limit)
    return query


def _get_log_level(event_type: str) -> str:
    """Map a Dagster event type to a log level (ERROR, WARNING, INFO, DEBUG)."""
    if "ERROR" in event_type or "FAILURE" in event_type:
        return "ERROR"
    elif "WARNING" in event_type:
        return "WARNING"
    elif "SUCCESS" in event_type or "OUTPUT" in event_type:
        return "INFO"
    else:
        return "DEBUG"
