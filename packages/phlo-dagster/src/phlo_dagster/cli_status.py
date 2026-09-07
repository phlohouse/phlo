"""Status command for Dagster assets and services.

This module implements the `phlo status` CLI command, providing visibility
into Dagster asset materialization status, freshness, and service health.
It queries the Dagster GraphQL API to retrieve real-time state information.

Features:
    - Asset status: Materialization state, last run time, and freshness
      indicators derived only from wired evidence sources; assets without a
      wired evidence source report unknown
    - Service health: Dagster, Trino, MinIO, Nessie connectivity checks
    - Filtering: By asset group, stale status
    - Output formats: Rich tables or JSON for scripting
    - Color-coded indicators for quick assessment

GraphQL Queries:
    The command queries Dagster's GraphQL API for:
    - Asset definitions and their metadata
    - Materialization history
    - Service health endpoints

Example:
    CLI usage::

        phlo status                    # All assets and services
        phlo status --assets           # Assets only
        phlo status --services         # Services only
        phlo status --group ingestion  # Filter by group
        phlo status --stale            # Only stale assets
        phlo status --json             # JSON output

"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import click
import requests as http_requests
from requests import exceptions as requests_exceptions
from rich.console import Console
from rich.table import Table

from phlo.cli.output import json_envelope
from phlo.cli.contract import PhloCommand
from phlo.config.env import load_project_env
from phlo.logging import get_logger
from phlo_dagster.settings import get_settings

console = Console()
logger = get_logger(__name__)

DEFAULT_SERVICE_PORTS = {
    "dagster": 3000,
    "trino": 8080,
    "minio": 9000,
    "nessie": 19120,
}


def _project_env() -> dict[str, str]:
    """Load project-level Phlo env files for CLI commands run on the host."""
    return load_project_env()


def _project_port(env: dict[str, str], key: str, default: int) -> int:
    """Resolve a project port override, falling back to the service default."""
    value = env.get(key)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("dagster_status_invalid_port_override", key=key, value=value)
        return default


def _dagster_graphql_url() -> str:
    """Resolve the Dagster GraphQL URL using project env overrides."""
    env = _project_env()
    settings = get_settings()
    dagster_host = env.get("DAGSTER_WEBSERVER_HOST", "localhost")
    dagster_port = _project_port(
        env,
        "DAGSTER_WEBSERVER_PORT",
        _project_port(env, "DAGSTER_PORT", settings.dagster_port),
    )
    return f"http://{dagster_host}:{dagster_port}/graphql"


def _service_health_urls() -> dict[str, str]:
    """Resolve service health URLs using project env overrides."""
    env = _project_env()
    trino_port = _project_port(env, "TRINO_PORT", DEFAULT_SERVICE_PORTS["trino"])
    minio_port = _project_port(env, "MINIO_API_PORT", DEFAULT_SERVICE_PORTS["minio"])
    nessie_port = _project_port(env, "NESSIE_PORT", DEFAULT_SERVICE_PORTS["nessie"])
    return {
        "trino": f"http://localhost:{trino_port}/v1/info",
        "minio": f"http://localhost:{minio_port}/minio/health/ready",
        "nessie": f"http://localhost:{nessie_port}/api/v1/config",
    }


@click.command(cls=PhloCommand)
@click.option(
    "--assets",
    is_flag=True,
    default=False,
    help="Show assets only",
)
@click.option(
    "--services",
    is_flag=True,
    default=False,
    help="Show services only",
)
@click.option(
    "--group",
    help="Filter by asset group",
)
@click.option(
    "--stale",
    is_flag=True,
    default=False,
    help="Show only stale assets",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="JSON output for scripting",
)
def status(
    assets: bool,
    services: bool,
    group: str | None,
    stale: bool,
    output_json: bool,
) -> None:
    """Show current state of assets, jobs, and services: asset
    materialization status and freshness, service health (Dagster, Trino,
    MinIO, Nessie), with color-coded indicators. Options narrow the view to
    assets or services, filter by group or staleness, and emit JSON for
    scripting; query failures are logged as warnings.
    """
    if not output_json:
        console.print("\n[bold blue]📊 Status Report[/bold blue]\n")

    start_time = time.time()

    # Show both by default if neither is specified
    show_assets = assets or (not assets and not services)
    show_services = services or (not assets and not services)
    logger.info(
        "dagster_status_command_started",
        show_assets=show_assets,
        show_services=show_services,
        group=group,
        stale_only=stale,
        output_json=output_json,
    )

    result = {}
    errors: list[str] = []

    if show_assets:
        try:
            asset_status = _get_asset_status(group=group, stale=stale)
        except click.ClickException as exc:
            errors.append(exc.format_message())
            asset_status = []
        result["assets"] = asset_status
        logger.info(
            "dagster_status_assets_collected",
            asset_count=len(asset_status),
            group=group,
            stale_only=stale,
        )
        if not output_json and not errors:
            _display_asset_status(asset_status, group=group, stale=stale)

    if show_services:
        service_status = _get_service_status()
        result["services"] = service_status
        logger.info(
            "dagster_status_services_collected",
            service_count=len(service_status),
        )
        if not output_json:
            _display_service_status(service_status)

    elapsed = time.time() - start_time

    if output_json:
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        result["elapsed_seconds"] = round(elapsed, 2)
        click.echo(
            json_envelope(
                data=result,
                errors=errors,
                status="partial" if errors and show_services else "error" if errors else "success",
                reason_code="status_incomplete" if errors else "status_collected",
                next_steps=[
                    {
                        "command": "phlo services status --json",
                        "when": "asset status is unavailable",
                    }
                ]
                if errors
                else [],
            )
        )
    else:
        console.print(f"[dim]Query time: {elapsed:.2f}s[/dim]\n")
    if errors:
        if not output_json:
            for error in errors:
                click.echo(f"Error: {error}", err=True)
        raise click.exceptions.Exit(1)
    logger.info(
        "dagster_status_command_finished",
        elapsed_seconds=round(elapsed, 3),
        show_assets=show_assets,
        show_services=show_services,
    )


def _get_asset_status(
    group: str | None = None,
    stale: bool = False,
) -> list[dict[str, Any]]:
    """
    Query the Dagster GraphQL API for asset status dicts with name,
    last_run, status, and freshness.
    """
    assets: list[dict[str, Any]] = []
    logger.debug(
        "dagster_status_asset_query_started",
        group=group,
        stale_only=stale,
    )

    try:
        dagster_url = _dagster_graphql_url()

        # Query asset materializations
        query = """
        {
            assetsOrError {
                __typename
                ... on AssetConnection {
                    nodes {
                        key {
                            path
                        }
                        definition {
                            groupName
                            description
                        }
                    }
                }
            }
        }
        """

        try:
            response = http_requests.post(dagster_url, json={"query": query}, timeout=5)
            response.raise_for_status()
            result = response.json()

            connection = (
                (result.get("data") or {}).get("assetsOrError")
                if isinstance(result, dict)
                else None
            )
            if (
                not isinstance(connection, dict)
                or result.get("errors")
                or connection.get("__typename") not in (None, "AssetConnection")
            ):
                raise click.ClickException("Dagster did not return an asset status result.")
            if result and "data" in result:
                for asset in result["data"].get("assetsOrError", {}).get("nodes", []):
                    if not isinstance(asset, dict):
                        continue
                    asset_key = asset.get("key") or {}
                    asset_definition = asset.get("definition") or {}
                    asset_path = asset_key.get("path", [])
                    asset_name = "/".join(asset_path) if asset_path else "unknown"
                    asset_group = asset_definition.get("groupName", "")

                    if group and asset_group != group:
                        continue

                    # Get last materialization from the wired evidence source
                    evidence = _get_asset_last_run(asset_name)
                    if evidence.available:
                        last_run = evidence.last_run
                        is_stale: bool | None = _check_if_stale(last_run)
                        status_value = (
                            last_run.get("status", "unknown") if last_run else "never_run"
                        )
                        freshness = _get_freshness_indicator(last_run)
                    else:
                        # No wired evidence source: report unknown, never a
                        # stub-derived never_run/stale state.
                        last_run = None
                        is_stale = None
                        status_value = "unknown"
                        freshness = "unknown"

                    if stale and is_stale is not True:
                        continue

                    status_info = {
                        "name": asset_name,
                        "group": asset_group,
                        "last_run": last_run,
                        "status": status_value,
                        "freshness": freshness,
                        "is_stale": is_stale,
                        "evidence_available": evidence.available,
                    }
                    assets.append(status_info)
        except requests_exceptions.RequestException as exc:
            # An unavailable query must not masquerade as an empty asset list.
            logger.warning(
                "dagster_status_asset_query_failed",
                group=group,
                stale_only=stale,
                error=str(exc),
            )
            raise click.ClickException("Could not query Dagster asset status.") from exc
        except click.ClickException:
            raise
        except Exception as exc:
            logger.warning(
                "dagster_status_asset_query_failed",
                group=group,
                stale_only=stale,
                exc_info=True,
            )

            raise click.ClickException("Could not read Dagster asset status.") from exc

    except click.ClickException:
        raise
    except Exception as exc:
        logger.info(
            "dagster_status_asset_query_client_unavailable",
            group=group,
            stale_only=stale,
            exc_info=True,
        )

        raise click.ClickException("Dagster asset status is unavailable.") from exc

    return assets


@dataclass(frozen=True, slots=True)
class AssetRunEvidence:
    """Per-asset run evidence behind the status report.

    ``available`` is False when no wired evidence source backs the asset. An
    unwired asset must display as unknown and must never inherit
    ``never_run``/``stale`` from a stub, a default, or an empty snapshot. When
    ``available`` is True, ``last_run`` is the wired record, or None when the
    wired source has no run for the asset (a legitimate ``never_run``).
    """

    available: bool
    last_run: dict[str, Any] | None = None


def _get_asset_last_run(asset_name: str) -> AssetRunEvidence:
    """Return the wired run evidence for an asset.

    No per-asset run-evidence source is wired into this command yet. The
    command therefore reports unknown instead of fabricating ``never_run`` or
    ``stale`` for every asset.
    """
    return AssetRunEvidence(available=False)


def _check_if_stale(last_run: dict[str, Any] | None) -> bool:
    """Check if asset is stale based on SLA."""
    if not last_run:
        return True

    if last_run.get("status") == "failure":
        return True

    last_run_time = last_run.get("timestamp")
    if not last_run_time:
        return True

    # Check if older than 24 hours
    age = datetime.now(timezone.utc) - last_run_time
    return age > timedelta(hours=24)


def _get_freshness_indicator(last_run: dict[str, Any] | None) -> str:
    """Get freshness indicator (fresh, stale, never)."""
    if not last_run:
        return "never_run"

    if last_run.get("status") == "failure":
        return "failed"

    last_run_time = last_run.get("timestamp")
    if not last_run_time:
        return "unknown"

    age = datetime.now(timezone.utc) - last_run_time

    if age < timedelta(hours=1):
        return "fresh"
    elif age < timedelta(hours=24):
        return "okay"
    else:
        return "stale"


def _get_service_status() -> dict[str, dict[str, Any]]:
    """Get service health status."""
    services: dict[str, dict[str, Any]] = {}
    health_urls = _service_health_urls()

    # Check Dagster
    services["dagster"] = _check_dagster_health(_dagster_graphql_url())

    # Check Trino
    services["trino"] = _check_service_health(
        health_urls["trino"],
        name="Trino",
    )

    # Check MinIO
    services["minio"] = _check_service_health(
        health_urls["minio"],
        name="MinIO",
    )

    # Check Nessie
    services["nessie"] = _check_service_health(
        health_urls["nessie"],
        name="Nessie",
    )
    logger.info("dagster_status_service_checks_completed", service_count=len(services))

    return services


def _check_dagster_health(url: str) -> dict[str, Any]:
    """Check Dagster health via GraphQL."""
    try:
        start = time.time()
        response = http_requests.post(url, json={"query": "{ version }"}, timeout=2)
        latency = (time.time() - start) * 1000
        try:
            body = response.json()
        except ValueError:
            body = None
        is_healthy = response.status_code == 200 and isinstance(body, dict) and "data" in body
        if not is_healthy:
            logger.warning(
                "dagster_status_service_health_unhealthy",
                service_name="Dagster",
                status_code=response.status_code,
                latency_ms=round(latency, 1),
            )
        return {
            "name": "Dagster",
            "status": "healthy" if is_healthy else "unhealthy",
            "latency_ms": round(latency, 1),
            "status_code": response.status_code,
        }
    except requests_exceptions.Timeout:
        logger.warning(
            "dagster_status_service_health_timeout",
            service_name="Dagster",
            timeout_seconds=2,
        )
        return {
            "name": "Dagster",
            "status": "timeout",
            "latency_ms": 2000,
            "error": "Request timeout",
        }
    except requests_exceptions.ConnectionError:
        logger.warning(
            "dagster_status_service_health_connection_error",
            service_name="Dagster",
        )
        return {
            "name": "Dagster",
            "status": "down",
            "latency_ms": None,
            "error": "Connection refused",
        }
    except requests_exceptions.RequestException as exc:
        logger.warning(
            "dagster_status_service_health_failed",
            service_name="Dagster",
            error=str(exc),
        )
        return {
            "name": "Dagster",
            "status": "error",
            "latency_ms": None,
            "error": str(exc),
        }


def _check_service_health(
    url: str,
    name: str,
) -> dict[str, Any]:
    """Check if a service is healthy."""
    try:
        start = time.time()
        response = http_requests.get(url, timeout=2)
        latency = (time.time() - start) * 1000  # Convert to ms

        is_healthy = 200 <= response.status_code < 300
        health_status = "healthy" if is_healthy else "unhealthy"
        if not is_healthy:
            logger.warning(
                "dagster_status_service_health_unhealthy",
                service_name=name,
                status_code=response.status_code,
                latency_ms=round(latency, 1),
            )

        return {
            "name": name,
            "status": health_status,
            "latency_ms": round(latency, 1),
            "status_code": response.status_code,
        }
    except requests_exceptions.Timeout:
        logger.warning(
            "dagster_status_service_health_timeout",
            service_name=name,
            timeout_seconds=2,
        )
        return {
            "name": name,
            "status": "timeout",
            "latency_ms": 2000,
            "error": "Request timeout",
        }
    except requests_exceptions.ConnectionError:
        logger.warning(
            "dagster_status_service_health_connection_error",
            service_name=name,
        )
        return {
            "name": name,
            "status": "down",
            "latency_ms": None,
            "error": "Connection refused",
        }
    except Exception as e:
        logger.error(
            "dagster_status_service_health_failed",
            service_name=name,
            error=str(e),
            exc_info=True,
        )
        return {
            "name": name,
            "status": "error",
            "latency_ms": None,
            "error": str(e),
        }


def _display_asset_status(
    assets: list[dict[str, Any]],
    group: str | None = None,
    stale: bool = False,
) -> None:
    """Display asset status table."""
    if not assets:
        console.print("[yellow]No assets found[/yellow]")
        return

    table = Table(title="Asset Status", show_header=True, header_style="bold blue")

    table.add_column("Asset Name", style="cyan")
    table.add_column("Group", style="magenta")
    table.add_column("Status", style="white")
    table.add_column("Last Run", style="green")
    table.add_column("Freshness", style="yellow")

    for asset in sorted(assets, key=lambda a: a["name"]):
        # Status color
        asset_status = asset["status"]
        if asset_status == "success":
            status_str = "[green]✓ success[/green]"
        elif asset_status == "failure":
            status_str = "[red]✗ failed[/red]"
        else:
            status_str = "[yellow]⚠ unknown[/yellow]"

        # Freshness color
        freshness = asset["freshness"]
        if freshness == "fresh":
            freshness_str = "[green]Fresh[/green]"
        elif freshness == "okay":
            freshness_str = "[yellow]Okay[/yellow]"
        elif freshness == "stale":
            freshness_str = "[red]Stale[/red]"
        elif freshness == "failed":
            freshness_str = "[red]Failed[/red]"
        elif freshness == "unknown":
            # No wired evidence source: never present unknown as never-run.
            freshness_str = "[dim]— unknown[/dim]"
        else:
            freshness_str = "[dim]Never run[/dim]"

        # Last run time
        last_run = asset.get("last_run")
        if freshness == "unknown":
            last_run_str = "[dim]—[/dim]"
        elif last_run and last_run.get("timestamp"):
            ts = last_run["timestamp"]
            age = datetime.now(timezone.utc) - ts
            if age < timedelta(hours=1):
                last_run_str = f"{int(age.total_seconds() / 60)}m ago"
            elif age < timedelta(days=1):
                last_run_str = f"{int(age.total_seconds() / 3600)}h ago"
            else:
                last_run_str = f"{int(age.days)}d ago"
        else:
            last_run_str = "[dim]Never[/dim]"

        table.add_row(
            asset["name"],
            asset.get("group", "-"),
            status_str,
            last_run_str,
            freshness_str,
        )

    console.print(table)


def _display_service_status(services: dict[str, dict[str, Any]]) -> None:
    """Display service health table."""
    table = Table(
        title="Service Health",
        show_header=True,
        header_style="bold blue",
    )

    table.add_column("Service", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Latency", style="yellow")

    for service_key in sorted(services.keys()):
        service = services[service_key]
        svc_status = service.get("status", "unknown")

        # Status color
        if svc_status == "healthy":
            status_str = "[green]✓ Healthy[/green]"
        elif svc_status == "down":
            status_str = "[red]✗ Down[/red]"
        elif svc_status == "timeout":
            status_str = "[yellow]⚠ Timeout[/yellow]"
        elif svc_status == "unhealthy":
            status_str = "[red]✗ Unhealthy[/red]"
        else:
            status_str = "[yellow]⚠ Error[/yellow]"

        # Latency
        latency = service.get("latency_ms")
        if latency:
            latency_str = f"{latency:.0f}ms"
        else:
            latency_str = "[dim]—[/dim]"

        table.add_row(
            service["name"],
            status_str,
            latency_str,
        )

    console.print(table)
