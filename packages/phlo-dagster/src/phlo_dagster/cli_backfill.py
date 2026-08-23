"""Backfill command for partitioned asset materialization.

This module implements the `phlo backfill` CLI command, enabling batch
materialization of partitioned Dagster assets across date ranges. It
supports parallel execution, resume capability, and dry-run preview.

Features:
    - Date range or explicit partition list modes
    - Parallel execution with configurable workers
    - Resume capability via state file persistence
    - Dry-run mode for previewing operations
    - Rate limiting with delay between executions
    - Progress tracking with Rich UI
    - Container backend execution

State Management:
    Backfill state is persisted to `.phlo/backfill_state.json` to enable
    resume after interruption. State includes asset name, completed
    partitions, and remaining work.

Execution:
    Backfills run via the selected container backend into the Dagster container, enabling
    access to the full Dagster environment while maintaining isolation
    from the host system.

Example:
    CLI usage::

        phlo backfill dlt_orders --start-date 2024-01-01 --end-date 2024-01-31
        phlo backfill dlt_orders --partitions 2024-01-01,2024-01-15,2024-01-31
        phlo backfill dlt_orders --start-date 2024-01-01 --end-date 2024-12-31 --parallel 4
        phlo backfill --resume
        phlo backfill dlt_orders --start-date 2024-01-01 --end-date 2024-01-31 --dry-run

"""

import json
import asyncio
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from phlo.cli.infrastructure.container_backend import (
    ContainerBackend,
    select_project_container_backend,
)
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import service_unavailable_error
from phlo.capabilities.discovery import discover_capabilities
from phlo.infrastructure import load_wap_config
from phlo.logging import get_logger
from phlo_dagster.cli_materialize import wait_for_dagster_runtime
from phlo_dagster.containers import find_dagster_container
from phlo_dagster.operations import get_run_status, launch_materialize
from phlo_dagster.wap_endpoint import resolve_wap_dagster_url
from phlo_dagster.wap_launch import prepare_wap_launch, read_wap_report

console = Console()
logger = get_logger(__name__)
BACKFILL_STATE_FILE = Path(".phlo/backfill_state.json")


@click.command(help="Run asset materialization across a date range with parallel execution.")
@click.argument("asset_name", required=False)
@click.option(
    "--start-date",
    type=str,
    help="Start date (YYYY-MM-DD)",
)
@click.option(
    "--end-date",
    type=str,
    help="End date (YYYY-MM-DD)",
)
@click.option(
    "--partitions",
    type=str,
    help="Comma-separated partition dates (YYYY-MM-DD,YYYY-MM-DD,...)",
)
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Number of concurrent partitions to process (default: 1; WAP runs serialize through promotion)",
)
@click.option(
    "--resume",
    is_flag=True,
    default=False,
    help="Resume last backfill, skipping completed partitions",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be executed without running",
)
@click.option(
    "--delay",
    type=float,
    default=0.0,
    help="Delay between parallel executions in seconds (rate limiting)",
)
def backfill(
    asset_name: str | None,
    start_date: str | None,
    end_date: str | None,
    partitions: str | None,
    parallel: int,
    resume: bool,
    dry_run: bool,
    delay: float,
):
    """Run asset materialization across a date range with parallel execution.

    Invoked either with a --start-date/--end-date range, explicit
    comma-separated --partitions, or --resume to continue an interrupted
    backfill from its persisted state. Exits non-zero on validation or
    backfill failure.
    """
    console.print("\n[bold blue]📦 Asset Backfill[/bold blue]\n")
    logger.info(
        "dagster_backfill_command_started",
        asset_name=asset_name,
        start_date=start_date,
        end_date=end_date,
        has_partitions=partitions is not None,
        parallel=parallel,
        resume=resume,
        dry_run=dry_run,
        delay=delay,
    )

    # Validate inputs
    if resume:
        # Resume mode: load from state file
        if not BACKFILL_STATE_FILE.exists():
            logger.error(
                "dagster_backfill_resume_state_missing",
                state_file=str(BACKFILL_STATE_FILE),
            )
            click.echo(
                "Error: No backfill state found. Cannot resume.",
                err=True,
            )
            sys.exit(1)

        try:
            state = _load_backfill_state()
            asset_name = state.get("asset_name")
            partition_dates = state.get("remaining_partitions", [])
            completed_partitions = state.get("completed_partitions", [])
            in_flight_wap = state.get("in_flight_wap", {})
        except Exception as e:
            logger.error(
                "dagster_backfill_resume_state_read_failed",
                state_file=str(BACKFILL_STATE_FILE),
                error=str(e),
                exc_info=True,
            )
            click.echo("Error: Could not read backfill state.", err=True)
            sys.exit(1)
    else:
        # Determine partition list
        if partitions:
            # Explicit partitions
            partition_dates = [p.strip() for p in partitions.split(",")]
            _validate_partition_dates(partition_dates)
        elif start_date and end_date:
            # Generate from date range
            partition_dates = _generate_partition_dates(start_date, end_date)
        else:
            logger.error("dagster_backfill_partitions_missing")
            click.echo(
                "Error: Must specify either --start-date/--end-date or --partitions",
                err=True,
            )
            sys.exit(1)

        if not asset_name:
            logger.error("dagster_backfill_asset_name_missing")
            click.echo("Error: Asset name is required", err=True)
            sys.exit(1)

        completed_partitions = []
        in_flight_wap = {}

    # Validate asset name
    if not asset_name:
        logger.error("dagster_backfill_asset_name_missing")
        click.echo("Error: Asset name is required", err=True)
        sys.exit(1)
    asset_name = str(asset_name)

    # Validate parallel value
    if parallel < 1:
        logger.error("dagster_backfill_parallel_invalid", parallel=parallel)
        click.echo(
            "Error: Parallel must be >= 1",
            err=True,
        )
        sys.exit(1)

    # Display backfill plan
    console.print(f"[cyan]Asset:[/cyan] {asset_name}")
    console.print(f"[cyan]Total partitions:[/cyan] {len(partition_dates)}")
    console.print(f"[cyan]Parallel workers:[/cyan] {parallel}")

    if completed_partitions:
        console.print(f"[yellow]Already completed:[/yellow] {len(completed_partitions)}")
        console.print(f"[yellow]Remaining:[/yellow] {len(partition_dates)}")

    wap_config = load_wap_config()

    if dry_run:
        logger.info(
            "dagster_backfill_dry_run",
            asset_name=asset_name,
            partition_count=len(partition_dates),
        )
        console.print("\n[yellow]Dry run - showing first 5 commands:[/yellow]\n")
        dagster_url = resolve_wap_dagster_url(wap_config) if wap_config.enabled else None
        for date in partition_dates[:5]:
            if wap_config.enabled:
                console.print(
                    f"[dim]GraphQL WAP launch {asset_name} partition {date} "
                    f"through {dagster_url}[/dim]"
                )
            else:
                cmd = _build_materialize_command(asset_name, date, container_name="dagster")
                console.print(f"[dim]{' '.join(cmd)}[/dim]")
        if len(partition_dates) > 5:
            console.print(f"[dim]... and {len(partition_dates) - 5} more[/dim]")
        return

    if not partition_dates:
        logger.info("dagster_backfill_no_partitions", asset_name=asset_name)
        console.print("[yellow]No partitions to backfill[/yellow]")
        return

    if wap_config.enabled:
        dagster_url = resolve_wap_dagster_url(wap_config)
        access_token = os.environ.get("PHLO_DAGSTER_ACCESS_TOKEN")
        if getattr(wap_config, "requires_access_token", False) and not access_token:
            raise click.ClickException(
                "PHLO_DAGSTER_ACCESS_TOKEN is required for a non-local WAP Dagster endpoint."
            )
        discover_capabilities()
        _run_wap_backfill(
            asset_name,
            partition_dates,
            dagster_url=dagster_url,
            job_name=wap_config.job_name,
            repository_location_name=wap_config.repository_location_name,
            repository_name=wap_config.repository_name,
            access_token=access_token,
            completed_partitions=completed_partitions,
            requested_parallel=parallel,
            in_flight_wap=in_flight_wap,
        )
        return

    # Run backfill with progress tracking
    console.print()
    _run_backfill(
        asset_name,
        partition_dates,
        parallel=parallel,
        delay=delay,
        completed_partitions=completed_partitions,
    )


def _run_wap_backfill(
    asset_name: str,
    partition_dates: list[str],
    *,
    dagster_url: str,
    job_name: str,
    repository_location_name: str | None,
    repository_name: str | None,
    access_token: str | None,
    completed_partitions: list[str] | None = None,
    requested_parallel: int = 1,
    in_flight_wap: dict[str, dict[str, str]] | None = None,
) -> None:
    """Run each WAP partition through promotion before creating the next branch.

    A WAP branch is based on ``main`` and can only be promoted against that
    snapshot. Consequently, partitions targeting one branch cannot safely
    overlap, even when the caller requested multiple workers.
    """
    completed_partitions = completed_partitions or []
    remaining = [date for date in partition_dates if date not in completed_partitions]
    successful: list[str] = []
    in_flight_wap = dict(in_flight_wap or {})
    if requested_parallel > 1:
        console.print(
            "[yellow]WAP backfills serialize partitions through promotion; "
            "--parallel is limited to 1 for this target.[/yellow]"
        )
    for partition_date in remaining:
        try:
            lifecycle = in_flight_wap.get(partition_date)
            if lifecycle:
                logical_run_id = lifecycle["logical_run_id"]
                dagster_run_id = lifecycle["dagster_run_id"]
                console.print(
                    f"Reconciling WAP backfill {partition_date} from logical run {logical_run_id}"
                )
            else:
                logical_run_id = f"backfill-{uuid.uuid4().hex}"
                launch = prepare_wap_launch(logical_run_id=logical_run_id)
                try:
                    result = asyncio.run(
                        launch_materialize(
                            dagster_url=dagster_url,
                            asset_key_path=asset_name,
                            job_name=job_name,
                            repository_location_name=repository_location_name,
                            repository_name=repository_name,
                            access_token=access_token,
                            partition_key=partition_date,
                            idempotency_key=logical_run_id,
                            tags=launch.tags,
                        )
                    )
                except Exception as exc:
                    launch.record_launch_result(status="launch_ambiguous", error=str(exc))
                    raise click.ClickException(
                        f"WAP launch outcome is ambiguous for {partition_date}: {exc}"
                    ) from exc
                if not result.accepted:
                    launch.record_launch_result(status="launch_rejected", error=result.message)
                    raise click.ClickException(result.message)
                if not launch.record_launch_result(
                    status="launched", dagster_run_id=getattr(result, "run_id", None)
                ):
                    raise click.ClickException(
                        "Dagster accepted the WAP run, but its immutable launch manifest could not be stored. "
                        "The branch was retained."
                    )
                dagster_run_id = getattr(result, "run_id", None)
                if not dagster_run_id:
                    raise click.ClickException(
                        "Dagster accepted the WAP run without returning a run ID."
                    )
                in_flight_wap[partition_date] = {
                    "logical_run_id": logical_run_id,
                    "dagster_run_id": dagster_run_id,
                }
                # Persist the in-flight mapping immediately after Dagster
                # accepts the run, so an interrupted CLI resumes by
                # reconciling that run instead of launching a duplicate.
                _save_backfill_state(
                    asset_name,
                    [
                        date
                        for date in partition_dates
                        if date not in completed_partitions + successful
                    ],
                    completed_partitions + successful,
                    in_flight_wap=in_flight_wap,
                    emit_log=False,
                )
            console.print(
                f"Waiting for WAP backfill {partition_date} (logical run {logical_run_id})"
            )
            _wait_for_wap_lifecycle(
                logical_run_id=logical_run_id,
                dagster_run_id=dagster_run_id,
                dagster_url=dagster_url,
                access_token=access_token,
            )
        except WapLifecycleTerminalError:
            in_flight_wap.pop(partition_date, None)
            _save_backfill_state(
                asset_name,
                [date for date in partition_dates if date not in completed_partitions + successful],
                completed_partitions + successful,
                in_flight_wap=in_flight_wap,
                emit_log=True,
            )
            raise
        except Exception:
            _save_backfill_state(
                asset_name,
                [date for date in partition_dates if date not in completed_partitions + successful],
                completed_partitions + successful,
                in_flight_wap=in_flight_wap,
                emit_log=True,
            )
            raise
        successful.append(partition_date)
        in_flight_wap.pop(partition_date, None)
        _save_backfill_state(
            asset_name,
            [date for date in partition_dates if date not in completed_partitions + successful],
            completed_partitions + successful,
            in_flight_wap=in_flight_wap,
            emit_log=False,
        )
    _remove_backfill_state()
    console.print("\n[green]✓ WAP backfill complete and promoted![/green]")


class WapLifecycleTerminalError(click.ClickException):
    """A run or promotion reached a terminal failure state."""


def _wait_for_wap_lifecycle(
    *, logical_run_id: str, dagster_run_id: str, dagster_url: str, access_token: str | None
) -> None:
    """Wait for both Dagster completion and the WAP promotion receipt."""
    timeout_seconds = float(os.environ.get("PHLO_WAP_BACKFILL_TIMEOUT_SECONDS", "3600"))
    poll_seconds = float(os.environ.get("PHLO_WAP_BACKFILL_POLL_SECONDS", "2"))
    deadline = time.monotonic() + timeout_seconds
    max_poll_failures = 5
    poll_failures = 0
    terminal_run_failures = {"FAILURE", "CANCELED", "CANCELING", "ABORTED"}
    terminal_promotion_failures = {"promotion_failed", "promotion_blocked", "launch_ambiguous"}
    while time.monotonic() < deadline:
        try:
            status = asyncio.run(
                get_run_status(
                    dagster_url=dagster_url, run_id=dagster_run_id, access_token=access_token
                )
            ).upper()
            report = read_wap_report(logical_run_id)
        except Exception as exc:
            poll_failures += 1
            # Transient poll failures (Dagster restarts, network blips) are
            # tolerated with capped exponential backoff before giving up;
            # the run itself is untouched and --resume can retry later.
            if poll_failures >= max_poll_failures:
                raise click.ClickException(
                    f"WAP lifecycle polling failed {poll_failures} times for logical run "
                    f"{logical_run_id}: {exc}"
                ) from exc
            time.sleep(min(poll_seconds * (2**poll_failures), 30))
            continue
        poll_failures = 0
        if status in terminal_run_failures:
            raise WapLifecycleTerminalError(
                f"WAP backfill Dagster run failed for logical run {logical_run_id}: {status}"
            )
        report_status = report.get("status") if report else None
        if report_status == "promoted":
            return
        if report_status in terminal_promotion_failures:
            reason = report.get("failure_reason", report_status) if report else report_status
            raise WapLifecycleTerminalError(
                f"WAP promotion failed for logical run {logical_run_id}: {reason}"
            )
        time.sleep(poll_seconds)
    raise click.ClickException(
        f"Timed out waiting for WAP lifecycle of logical run {logical_run_id}; "
        "run phlo backfill --resume to retry the partition."
    )


def _generate_partition_dates(start_date: str, end_date: str) -> list[str]:
    """
    Generate the YYYY-MM-DD partition dates between start_date and
    end_date, inclusive.
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        logger.error(
            "dagster_backfill_date_parse_failed",
            start_date=start_date,
            end_date=end_date,
        )
        click.echo(
            "Error: Invalid date format. Use YYYY-MM-DD",
            err=True,
        )
        sys.exit(1)

    if start > end:
        logger.error(
            "dagster_backfill_date_range_invalid",
            start_date=start_date,
            end_date=end_date,
        )
        click.echo(
            "Error: Start date must be before end date",
            err=True,
        )
        sys.exit(1)

    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    return dates


def _validate_partition_dates(dates: list[str]) -> None:
    """
    Validate that every partition date is in YYYY-MM-DD format; exits
    non-zero on the first invalid date.
    """
    for date in dates:
        try:
            datetime.strptime(date.strip(), "%Y-%m-%d")
        except ValueError:
            logger.error(
                "dagster_backfill_partition_date_invalid",
                partition_date=date,
            )
            click.echo(
                f"Error: Invalid partition date: {date}. Use YYYY-MM-DD",
                err=True,
            )
            sys.exit(1)


def _build_materialize_command(
    asset_name: str,
    partition_date: str,
    container_name: str | None = None,
    backend: ContainerBackend | None = None,
) -> list[str]:
    """
    Build the container exec command that materializes asset_name for one
    partition date.
    """
    import platform

    if container_name is None:
        project_name = get_project_name()
        container_name = find_dagster_container(project_name)
    host_platform = platform.system()
    selected_backend = backend or select_project_container_backend()

    return selected_backend.container_exec_cmd(
        container_name=container_name,
        user=f"{os.getuid()}:{os.getgid()}" if host_platform == "Linux" else None,
        env={
            "PHLO_HOST_PLATFORM": host_platform,
            "PHLO_PROJECT_PATH": "/app",
        },
        workdir="/app",
        command=[
            "dagster",
            "asset",
            "materialize",
            "-m",
            "phlo_dagster.framework.definitions",
            "--select",
            asset_name,
            "--partition",
            partition_date,
        ],
    )


def _run_backfill(
    asset_name: str,
    partition_dates: list[str],
    parallel: int = 1,
    delay: float = 0.0,
    completed_partitions: list[str] | None = None,
) -> None:
    """
    Execute the backfill across partition dates with a worker pool,
    persisting state periodically so an interrupted run can resume.
    """
    if completed_partitions is None:
        completed_partitions = []

    # Filter out completed partitions
    remaining = [d for d in partition_dates if d not in completed_partitions]
    total = len(partition_dates)
    already_done = len(completed_partitions)

    successful: list[str] = []
    failed: list[dict[str, str]] = []
    start_time = datetime.now(timezone.utc).isoformat()
    logger.info(
        "dagster_backfill_execution_started",
        asset_name=asset_name,
        total_partitions=total,
        remaining_partitions=len(remaining),
        completed_partitions=already_done,
        parallel=parallel,
        delay=delay,
    )
    try:
        backend = select_project_container_backend()
        container_name = find_dagster_container(get_project_name())
        wait_for_dagster_runtime(container_name, backend=backend)
    except FileNotFoundError as exc:
        logger.error(
            "dagster_backfill_service_unavailable",
            asset_name=asset_name,
            error=str(exc),
            exc_info=True,
        )
        raise service_unavailable_error("dagster") from exc
    except RuntimeError as exc:
        logger.error(
            "dagster_backfill_service_unavailable",
            asset_name=asset_name,
            error=str(exc),
            exc_info=True,
        )
        raise click.ClickException(str(exc)) from exc

    # Use ThreadPoolExecutor for parallel execution
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task(f"[cyan]Backfilling {asset_name}...", total=total)

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            # Submit all tasks
            future_to_date = {
                executor.submit(
                    _materialize_partition,
                    asset_name,
                    date,
                    delay if i > 0 else 0,
                    container_name,
                    backend,
                ): date
                for i, date in enumerate(remaining)
            }

            # Process completed tasks
            completed_count = already_done
            for future in as_completed(future_to_date):
                date = future_to_date[future]
                try:
                    success, output = future.result()
                    if success:
                        successful.append(date)
                        completed_count += 1
                        progress.update(
                            task,
                            completed=completed_count,
                            description=f"[green]✓ Completed {completed_count}/{total}[/green]",
                        )
                    else:
                        logger.warning(
                            "dagster_backfill_partition_failed",
                            asset_name=asset_name,
                            partition_date=date,
                        )
                        failed.append({"date": date, "error": output})
                        progress.update(
                            task,
                            description=f"[yellow]⚠ Failed {date}[/yellow]",
                        )
                except Exception as e:
                    logger.error(
                        "dagster_backfill_partition_execution_failed",
                        asset_name=asset_name,
                        partition_date=date,
                        error=str(e),
                        exc_info=True,
                    )
                    failed.append({"date": date, "error": str(e)})
                    progress.update(
                        task,
                        description=f"[red]✗ Error {date}[/red]",
                    )

                # Update state file periodically
                _save_backfill_state(asset_name, remaining, successful, emit_log=False)

    # Display results
    console.print()
    results = {
        "asset_name": asset_name,
        "start_time": start_time,
        "total_partitions": total,
        "completed_partitions": completed_partitions,
        "successful": successful,
        "failed": failed,
    }
    _display_backfill_results(results)
    logger.info(
        "dagster_backfill_execution_finished",
        asset_name=asset_name,
        total_partitions=total,
        successful=len(successful),
        failed=len(failed),
    )

    # Clean up state file on success
    if not failed:
        _remove_backfill_state()
    else:
        # Save final state for resume
        remaining_after = [d for d in partition_dates if d not in successful]
        _save_backfill_state(asset_name, remaining_after, successful, emit_log=True)


def _load_backfill_state() -> dict[str, Any]:
    """Read the persisted backfill state file and return its dictionary;
    failures to read are logged and re-raised.
    """
    logger.info(
        "dagster_backfill_state_load_started",
        state_file=str(BACKFILL_STATE_FILE),
    )
    try:
        state = json.loads(BACKFILL_STATE_FILE.read_text())
        logger.info(
            "dagster_backfill_state_load_completed",
            state_file=str(BACKFILL_STATE_FILE),
            asset_name=state.get("asset_name"),
            remaining_partition_count=len(state.get("remaining_partitions", [])),
            completed_partition_count=len(state.get("completed_partitions", [])),
        )
        return state
    except Exception as exc:
        logger.error(
            "dagster_backfill_state_load_failed",
            state_file=str(BACKFILL_STATE_FILE),
            error=str(exc),
            exc_info=True,
        )
        raise


def _materialize_partition(
    asset_name: str,
    partition_date: str,
    delay: float = 0.0,
    container_name: str | None = None,
    backend: ContainerBackend | None = None,
) -> tuple[bool, str]:
    """
    Materialize a single partition after an optional delay, returning a
    (success, output_message) pair; timeouts and container errors count as
    failure, not exceptions.
    """
    import time

    if delay > 0:
        time.sleep(delay)

    cmd = _build_materialize_command(
        asset_name,
        partition_date,
        container_name=container_name,
        backend=backend,
    )
    logger.debug(
        "dagster_backfill_partition_materialize_started",
        asset_name=asset_name,
        partition_date=partition_date,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour timeout per partition
        )

        if result.returncode == 0:
            return True, f"Materialized {partition_date}"
        else:
            error_msg = _summarize_materialize_error(result.stderr or result.stdout)
            logger.warning(
                "dagster_backfill_partition_materialize_nonzero_exit",
                asset_name=asset_name,
                partition_date=partition_date,
                returncode=result.returncode,
            )
            return False, error_msg
    except subprocess.TimeoutExpired:
        logger.warning(
            "dagster_backfill_partition_materialize_timeout",
            asset_name=asset_name,
            partition_date=partition_date,
            timeout_seconds=3600,
        )
        return False, f"Timeout after 1 hour for partition {partition_date}"
    except FileNotFoundError:
        logger.error(
            "dagster_backfill_partition_materialize_binary_missing",
            asset_name=asset_name,
            partition_date=partition_date,
        )
        return False, "Container backend not found or container not running"
    except Exception as e:
        logger.error(
            "dagster_backfill_partition_materialize_failed",
            asset_name=asset_name,
            partition_date=partition_date,
            error=str(e),
            exc_info=True,
        )
        return False, str(e)


def _summarize_materialize_error(output: str) -> str:
    """Extract a concise root-cause line from Dagster materialization output."""
    if not output.strip():
        return "materialization failed"

    patterns = (
        r"dagster\.[\w.]+:\s*(?P<message>.+)",
        r"dagster_shared\.[\w.]+:\s*(?P<message>.+)",
        r"CheckError:\s*(?P<message>.+)",
        r"Error:\s*(?P<message>.+)",
    )
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("{"):
            continue
        for pattern in patterns:
            match = re.search(pattern, stripped)
            if match:
                return match.group("message").strip()
        if "Traceback " in stripped or stripped.startswith("File "):
            continue
        if stripped.startswith(("WARNING:", "INFO:", "DEBUG:")):
            continue
        return stripped

    return output.strip().splitlines()[-1][:200]


def _save_backfill_state(
    asset_name: str,
    remaining_partitions: list[str],
    completed_partitions: list[str],
    in_flight_wap: dict[str, dict[str, str]] | None = None,
    emit_log: bool = False,
) -> None:
    """
    Persist backfill state (asset name, remaining/completed partitions, and
    any in-flight WAP runs) so an interrupted run can resume.
    """
    state_dir = BACKFILL_STATE_FILE.parent
    state_dir.mkdir(exist_ok=True)

    state = {
        "asset_name": asset_name,
        "remaining_partitions": remaining_partitions,
        "completed_partitions": completed_partitions,
        "in_flight_wap": in_flight_wap or {},
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    if emit_log:
        logger.info(
            "dagster_backfill_state_save_started",
            state_file=str(BACKFILL_STATE_FILE),
            asset_name=asset_name,
            remaining_partition_count=len(remaining_partitions),
            completed_partition_count=len(completed_partitions),
        )

    try:
        BACKFILL_STATE_FILE.write_text(json.dumps(state, indent=2))
        if emit_log:
            logger.info(
                "dagster_backfill_state_save_completed",
                state_file=str(BACKFILL_STATE_FILE),
                asset_name=asset_name,
                remaining_partition_count=len(remaining_partitions),
                completed_partition_count=len(completed_partitions),
            )
    except Exception as exc:
        logger.error(
            "dagster_backfill_state_save_failed",
            state_file=str(BACKFILL_STATE_FILE),
            asset_name=asset_name,
            remaining_partition_count=len(remaining_partitions),
            completed_partition_count=len(completed_partitions),
            error=str(exc),
            exc_info=True,
        )
        raise


def _remove_backfill_state() -> None:
    """Delete the persisted backfill state file if it exists; failures are
    logged and re-raised.
    """
    if not BACKFILL_STATE_FILE.exists():
        return
    logger.info(
        "dagster_backfill_state_remove_started",
        state_file=str(BACKFILL_STATE_FILE),
    )
    try:
        BACKFILL_STATE_FILE.unlink()
        logger.info(
            "dagster_backfill_state_remove_completed",
            state_file=str(BACKFILL_STATE_FILE),
        )
    except Exception as exc:
        logger.error(
            "dagster_backfill_state_remove_failed",
            state_file=str(BACKFILL_STATE_FILE),
            error=str(exc),
            exc_info=True,
        )
        raise


def _display_backfill_results(results: dict[str, Any]) -> None:
    """
    Print backfill results as a summary table plus per-partition failures;
    exits non-zero when any partition failed.
    """
    successful = len(results["successful"])
    failed = len(results["failed"])
    total = results["total_partitions"]

    console.print("[bold blue]Backfill Results[/bold blue]\n")

    # Summary
    table = Table(show_header=False)
    table.add_row("[cyan]Asset[/cyan]", results["asset_name"])
    table.add_row(
        "[cyan]Status[/cyan]",
        "[green]✓ Success[/green]" if failed == 0 else "[yellow]⚠ Partial[/yellow]",
    )
    table.add_row("[cyan]Completed[/cyan]", f"[green]{successful}[/green]")
    table.add_row("[cyan]Failed[/cyan]", f"[red]{failed}[/red]" if failed > 0 else "0")
    table.add_row("[cyan]Total[/cyan]", str(total))

    console.print(table)

    # Show failures if any
    if results["failed"]:
        console.print("\n[bold yellow]Failed Partitions[/bold yellow]\n")
        fail_table = Table(show_header=True, header_style="bold")
        fail_table.add_column("Date", style="cyan")
        fail_table.add_column("Error", style="red")

        for item in results["failed"]:
            if isinstance(item, dict):
                date = item.get("date", "unknown")
                error = item.get("error", "unknown error")
            else:
                date = item
                error = "unknown"

            fail_table.add_row(date, error[:200])

        console.print(fail_table)

        console.print("\n[yellow]To resume, run: phlo backfill --resume[/yellow]")
        sys.exit(1)

    console.print("\n[green]✓ Backfill complete![/green]")
