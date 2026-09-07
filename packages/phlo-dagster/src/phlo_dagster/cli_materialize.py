"""Materialize command for Dagster assets via the configured container backend.

This module implements the `phlo materialize` CLI command, providing a
convenient interface for materializing Dagster assets through Docker or
Podman container execution. It handles environment setup and passes
through to Dagster's asset materialization CLI.

Features:
    - Single asset or asset selection expression materialization
    - Partition support for time-sliced assets
    - Schema contract refresh control
    - Dry-run mode for command preview
    - Platform-aware environment injection (PHLO_HOST_PLATFORM)
    - Automatic Dagster container discovery
    - Streaming output from container execution

Environment Variables:
    - PHLO_HOST_PLATFORM: Host OS platform for DuckDB compatibility
    - PHLO_PROJECT_PATH: Project path within container
    - PHLO_AUTO_REFRESH_CONTRACTS: Enable schema contract refresh
    - PHLO_CONTRACT_REFRESH_SELECTION: Assets to refresh contracts for

Container Integration:
    The command uses the selected project container backend to run Dagster
CLI commands within the running Dagster container. This ensures:
    - Access to configured resources (Trino, MinIO, etc.)
    - Consistent Python environment
    - Proper logging context

Example:
    CLI usage::

        phlo materialize dlt_orders
        phlo materialize dlt_orders --partition 2025-01-15
        phlo materialize --select "tag:bronze"
        phlo materialize dlt_orders --dry-run
        phlo materialize dlt_orders --no-contract-refresh

"""

from __future__ import annotations

import asyncio
import os
import platform
import shlex
import subprocess
import sys
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Optional

import click

from phlo.capabilities.discovery import discover_capabilities
from phlo.cli.infrastructure.container_backend import (
    ContainerBackend,
    select_project_container_backend,
)
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import command_failed_error, service_unavailable_error, json_envelope
from phlo.cli.contract import PhloCommand
from phlo.infrastructure import load_wap_config
from phlo_dagster.containers import find_dagster_container
from phlo_dagster.operations import launch_materialize
from phlo_dagster.wap_endpoint import resolve_wap_dagster_url
from phlo_dagster.wap_launch import prepare_wap_launch
from phlo.logging import get_logger


def _summarize_process_output(lines: list[str]) -> str | None:
    """Return a short human-readable failure hint from process output."""
    for line in reversed(lines):
        message = line.strip()
        if message and not message.startswith("{"):
            return message[:240]
    return None


def wait_for_dagster_runtime(
    container_name: str,
    timeout_seconds: float = 600.0,
    backend: ContainerBackend | None = None,
) -> None:
    """Wait until the Dagster container has finished entrypoint setup."""
    selected_backend = backend or select_project_container_backend()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            selected_backend.container_exec_cmd(
                container_name=container_name,
                command=[
                    "sh",
                    "-lc",
                    "test -f /tmp/phlo-dagster-ready "
                    "|| python -c 'import phlo_dagster.framework.definitions'",
                ],
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(1)

    raise RuntimeError(
        "Dagster container is still finishing runtime setup. "
        "Inspect startup logs with: phlo services logs --tail 120 dagster"
    )


@click.command(
    cls=PhloCommand, help="Materialize Dagster assets via the configured container backend."
)
@click.argument("asset_name", required=False)
@click.option("-p", "--partition", help="Partition date (YYYY-MM-DD)")
@click.option(
    "--no-default-partition",
    is_flag=True,
    help="Do not default the partition to today when --partition is omitted",
)
@click.option("--select", help="Asset selector expression")
@click.option(
    "--no-contract-refresh",
    is_flag=True,
    help="Skip automatic schema contract refresh before materialization",
)
@click.option("--dry-run", is_flag=True, help="Show command without executing")
@click.option("--json", "output_json", is_flag=True, help="Emit a structured result.")
def materialize(
    asset_name: str | None,
    partition: Optional[str],
    no_default_partition: bool,
    select: Optional[str],
    no_contract_refresh: bool,
    dry_run: bool,
    output_json: bool = False,
) -> None:
    """Materialize Dagster assets via the configured container backend.

    select overrides asset_name; no_contract_refresh skips automatic schema
    contract refresh; dry_run prints the command without executing. When the
    selected asset is partitioned, an omitted --partition defaults to today
    (UTC) so ad-hoc runs never execute partitioned assets unpartitioned;
    pass --no-default-partition to opt out.

    Raises: SystemExit on command failure or when the container backend is
    not found.
    """
    if not asset_name and not select:
        raise click.UsageError("Provide ASSET_NAME or --select.")
    if partition is None and not no_default_partition:
        partition = datetime.now(UTC).strftime("%Y-%m-%d")

    wap_config = load_wap_config()
    if wap_config.enabled:
        if not asset_name or select:
            raise click.UsageError(
                "WAP is enabled in phlo.yaml and requires one ASSET_NAME; --select is not supported."
            )
        dagster_url = resolve_wap_dagster_url(wap_config)
        access_token = os.environ.get("PHLO_DAGSTER_ACCESS_TOKEN")
        if getattr(wap_config, "requires_access_token", False) and not access_token:
            raise click.ClickException(
                "PHLO_DAGSTER_ACCESS_TOKEN is required for a non-local WAP Dagster endpoint."
            )
        logical_run_id = uuid.uuid4().hex
        if dry_run:
            if output_json:
                click.echo(
                    json_envelope(
                        data={
                            "asset_name": asset_name,
                            "partition": partition,
                            "dagster_url": dagster_url,
                            "mode": "wap",
                        },
                        status="planned",
                        reason_code="materialization_planned",
                    )
                )
                return
            click.echo(
                "WAP dry run - would launch "
                f"{asset_name} with logical run ID {logical_run_id} through {dagster_url}"
            )
            return

        discover_capabilities()
        wap_launch = prepare_wap_launch(logical_run_id=logical_run_id)
        try:
            result = asyncio.run(
                launch_materialize(
                    dagster_url=dagster_url,
                    asset_key_path=asset_name,
                    job_name=wap_config.job_name,
                    repository_location_name=wap_config.repository_location_name,
                    repository_name=wap_config.repository_name,
                    access_token=access_token,
                    partition_key=partition,
                    idempotency_key=logical_run_id,
                    tags=wap_launch.tags,
                )
            )
        except Exception as exc:
            wap_launch.record_launch_result(status="launch_ambiguous", error=str(exc))
            raise click.ClickException(f"WAP launch outcome is ambiguous: {exc}") from exc
        if not result.accepted:
            wap_launch.record_launch_result(status="launch_rejected", error=result.message)
            raise click.ClickException(result.message)

        if not getattr(result, "run_id", None):
            wap_launch.record_launch_result(
                status="launch_ambiguous", error="Dagster returned no run ID"
            )
            raise click.ClickException(
                "Dagster accepted the request without a run ID; its outcome is unknown."
            )

        if not wap_launch.record_launch_result(status="launched", dagster_run_id=result.run_id):
            if output_json:
                click.echo(
                    json_envelope(
                        data={
                            "run_id": result.run_id,
                            "logical_run_id": logical_run_id,
                            "branch": wap_launch.branch,
                        },
                        status="partial",
                        reason_code="launch_manifest_failed",
                        errors=[
                            "Dagster accepted the run, but its immutable launch manifest could not be stored. The branch was retained."
                        ],
                    )
                )
                raise click.exceptions.Exit(1)
            raise click.ClickException(
                "Dagster accepted the WAP run, but its immutable launch manifest could not be stored. "
                "The branch was retained."
            )

        if output_json:
            click.echo(
                json_envelope(
                    data={
                        "asset_name": asset_name,
                        "partition": partition,
                        "logical_run_id": logical_run_id,
                        "run_id": result.run_id,
                        "branch": wap_launch.branch,
                    },
                    status="submitted",
                    reason_code="materialization_submitted",
                )
            )
            return
        click.echo(
            f"Launched WAP materialization for {asset_name} on {wap_launch.branch} "
            f"(logical run {logical_run_id}, Dagster run {result.run_id})"
        )
        return
    effective_selection = select or asset_name or ""

    logger = get_logger("phlo.dagster.materialize", service="dagster")
    started_at = time.perf_counter()
    project_name = get_project_name()
    container_name = "dagster"
    logger.info(
        "dagster_materialize_command_started",
        asset_name=effective_selection,
        partition=partition,
        select=select,
        no_contract_refresh=no_contract_refresh,
        dry_run=dry_run,
        project_name=project_name,
    )

    try:
        host_platform = platform.system()
        backend = select_project_container_backend()

        container_name = find_dagster_container(project_name)
        if not dry_run:
            wait_for_dagster_runtime(container_name, backend=backend)

        cmd = backend.container_exec_cmd(
            container_name=container_name,
            user=f"{os.getuid()}:{os.getgid()}" if host_platform == "Linux" else None,
            env={
                "PHLO_HOST_PLATFORM": host_platform,
                "PHLO_PROJECT_PATH": "/app",
                "PHLO_AUTO_REFRESH_CONTRACTS": "0" if no_contract_refresh else "1",
                "PHLO_CONTRACT_REFRESH_SELECTION": effective_selection,
            },
            workdir="/app",
            command=[
                "dagster",
                "asset",
                "materialize",
                "-m",
                "phlo_dagster.framework.definitions",
            ],
        )

        cmd.extend(["--select", effective_selection])

        if partition:
            cmd.extend(["--partition", partition])

        if dry_run:
            if output_json:
                click.echo(
                    json_envelope(
                        data={
                            "selection": effective_selection,
                            "partition": partition,
                            "argv": cmd,
                        },
                        status="planned",
                        reason_code="materialization_planned",
                    )
                )
                return
            click.echo("Dry run - would execute:\n")
            click.echo(shlex.join(cmd))
            logger.info(
                "dagster_materialize_command_completed",
                asset_name=effective_selection,
                partition=partition,
                select=select,
                no_contract_refresh=no_contract_refresh,
                dry_run=True,
                project_name=project_name,
                container_name=container_name,
                duration_seconds=round(time.perf_counter() - started_at, 3),
                returncode=0,
            )
            sys.exit(0)

        if not output_json:
            click.echo(f"Materializing {effective_selection}...\n")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        recent_output: deque[str] = deque(maxlen=20)
        if process.stdout:
            for line in process.stdout:
                message = line.rstrip()
                if message:
                    recent_output.append(message)
                    logger.debug(
                        "dagster_materialize_process_output",
                        source="dagster",
                        line=message,
                    )
        returncode = process.wait()
        if returncode == 0:
            if output_json:
                click.echo(
                    json_envelope(
                        data={
                            "selection": effective_selection,
                            "partition": partition,
                            "returncode": returncode,
                        },
                        reason_code="materialization_completed",
                    )
                )
            else:
                click.echo(f"\nSuccessfully materialized {effective_selection}")
            logger.info(
                "dagster_materialize_command_completed",
                asset_name=effective_selection,
                partition=partition,
                select=select,
                no_contract_refresh=no_contract_refresh,
                dry_run=False,
                project_name=project_name,
                container_name=container_name,
                duration_seconds=round(time.perf_counter() - started_at, 3),
                returncode=returncode,
            )
        else:
            output_hint = _summarize_process_output(list(recent_output))
            logger.error(
                "dagster_materialize_command_failed",
                asset_name=effective_selection,
                partition=partition,
                select=select,
                no_contract_refresh=no_contract_refresh,
                dry_run=False,
                project_name=project_name,
                container_name=container_name,
                duration_seconds=round(time.perf_counter() - started_at, 3),
                returncode=returncode,
            )
            raise command_failed_error(
                "materialization",
                exit_code=returncode,
                details=[f"Last output: {output_hint}"] if output_hint else None,
                run="phlo logs --service dagster --tail 20",
            )
    except FileNotFoundError:
        logger.error(
            "dagster_materialize_command_failed",
            asset_name=effective_selection,
            partition=partition,
            select=select,
            no_contract_refresh=no_contract_refresh,
            dry_run=dry_run,
            project_name=project_name,
            duration_seconds=round(time.perf_counter() - started_at, 3),
            error="docker_not_found_or_container_not_running",
            exc_info=True,
        )
        raise service_unavailable_error(container_name) from None
    except RuntimeError as exc:
        logger.error(
            "dagster_materialize_service_unavailable",
            asset_name=effective_selection,
            partition=partition,
            select=select,
            no_contract_refresh=no_contract_refresh,
            dry_run=dry_run,
            project_name=project_name,
            duration_seconds=round(time.perf_counter() - started_at, 3),
            error=str(exc),
            exc_info=True,
        )
        raise service_unavailable_error("dagster") from exc
    except click.ClickException:
        raise
    except Exception as exc:
        logger.error(
            "dagster_materialize_command_failed",
            asset_name=effective_selection,
            partition=partition,
            select=select,
            no_contract_refresh=no_contract_refresh,
            dry_run=dry_run,
            project_name=project_name,
            duration_seconds=round(time.perf_counter() - started_at, 3),
            error=str(exc),
            exc_info=True,
        )
        raise
