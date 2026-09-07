"""List command for showing available services.

Combines declared compose services with live container state from the selected
backend, reporting enabled/disabled flags, ports, and running status per
service.
"""

from pathlib import Path

import click
import yaml

from phlo.cli.commands.services.ports import _parse_compose_port_spec
from phlo.cli.commands.services.utils import get_enabled_disabled_service_names
from phlo.cli.contract import PhloCommand
from phlo.cli.infrastructure.container_backend import select_project_container_backend
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import json_envelope, user_error
from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

logger = get_logger(__name__)


def _get_running_containers(
    project_name: str,
    backend_name: str | None = None,
) -> dict[str, dict[str, str]]:
    """Get running container status from the selected backend."""
    backend = select_project_container_backend(cli_backend=backend_name)
    containers: dict[str, dict[str, str]] = {}
    for container in backend.list_project_containers(project_name):
        containers[container.service] = {
            "status": container.state,
            "ports": container.ports,
        }
    return containers


def _first_declared_container_port(svc: ServiceDefinition) -> str | None:
    """Return the first container port declared by the service compose config."""
    for entry in svc.compose.get("ports", []) or []:
        if isinstance(entry, str):
            port_spec = _parse_compose_port_spec(entry)
            if port_spec.container_port.isdigit():
                return port_spec.container_port
        elif isinstance(entry, dict):
            target = entry.get("target")
            if target is not None and str(target).isdigit():
                return str(target)
    return None


def _external_port_for_container(
    port_str: str,
    preferred_container_port: str | None,
) -> str:
    """Extract a host port, preferring the service's first declared container port."""
    segments = [part.strip() for part in port_str.split(",") if "->" in part]
    if not segments:
        return ""

    if preferred_container_port:
        suffix = f"->{preferred_container_port}/"
        for segment in segments:
            if suffix in segment:
                return segment.split("->", 1)[0].split(":")[-1]

    return segments[0].split("->", 1)[0].split(":")[-1]


@click.command("list", cls=PhloCommand)
@click.option("--all", "show_all", is_flag=True, help="Show all services including optional")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for runtime status.",
)
def list_cmd(show_all: bool, output_json: bool, backend_name: str | None):
    """List available services with status and configuration.

    Examples:
        phlo services list
        phlo services list --all
        phlo services list --json
    """
    # Load phlo.yaml for user overrides
    config_file = Path.cwd() / "phlo.yaml"
    existing_config: dict = {}
    user_overrides = {}
    logger.info(
        "services_list_requested",
        show_all=show_all,
        output_json=output_json,
    )
    if config_file.exists():
        try:
            with config_file.open() as f:
                existing_config = yaml.safe_load(f) or {}
                if not isinstance(existing_config, dict):
                    raise user_error(
                        f"{config_file} must contain a top-level mapping.",
                        reason_code="invalid_project_config",
                    )
                user_overrides = existing_config.get("services", {})
                if not isinstance(user_overrides, dict):
                    user_overrides = {}
        except (OSError, yaml.YAMLError) as exc:
            logger.error(
                "services_list_config_read_failed",
                config_file=str(config_file),
                exc_info=True,
            )
            raise user_error(
                f"Failed to read {config_file}. Check YAML syntax and file permissions, then retry.",
                reason_code="invalid_project_config",
            ) from exc

    # Discover available services
    try:
        discovery = ServiceDiscovery()
        available_services = discovery.discover()
    except Exception as exc:
        logger.error("services_list_discovery_failed", exc_info=True)
        raise user_error(
            "Failed to discover services. Verify service plugins are installed.",
            reason_code="plugin_discovery_failed",
            run="phlo plugin list",
        ) from exc

    # Check which services are explicitly enabled/disabled.
    enabled_services, disabled_services = get_enabled_disabled_service_names(existing_config)

    # Collect inline custom services
    inline_services = []
    for name, cfg in user_overrides.items():
        if isinstance(cfg, dict) and cfg.get("type") == "inline":
            inline_services.append(ServiceDefinition.from_inline(name, cfg))

    # Get running container status using compose project label for deterministic matching
    runtime_available = True
    warnings = []
    try:
        project_name = get_project_name()
        running_containers = _get_running_containers(project_name, backend_name)
    except (FileNotFoundError, OSError, ValueError):
        logger.warning(
            "services_list_runtime_status_unavailable",
            project_name=get_project_name(),
            exc_info=True,
        )
        running_containers = {}
        runtime_available = False
        warnings.append("Runtime status unavailable. Check your container backend and retry.")

    # Separate services by type
    package_services = [
        s for s in available_services.values() if not s.core or s.name in disabled_services
    ]
    visible_services = [
        svc
        for svc in sorted(package_services, key=lambda x: x.name)
        if show_all
        or not svc.profile
        or svc.default
        or svc.name in enabled_services
        or svc.name in disabled_services
        or svc.name in running_containers
    ]
    if output_json:
        all_services = visible_services + sorted(inline_services, key=lambda x: x.name)
        payload = [
            {
                "name": svc.name,
                "description": svc.description,
                "category": svc.category,
                "default": svc.default,
                "profile": svc.profile,
                "depends_on": svc.depends_on,
                "compose": svc.compose,
                "env_vars": svc.env_vars,
                "core": svc.core,
                "disabled": svc.name in disabled_services,
                "inline": svc in inline_services,
                "running": (running_containers.get(svc.name, {}).get("status") == "running")
                if runtime_available
                else None,
                "state": running_containers.get(svc.name, {}).get("status", "stopped")
                if runtime_available
                else "unknown",
            }
            for svc in all_services
        ]
        logger.info(
            "services_list_json_completed",
            total_services=len(payload),
            running_count=sum(1 for svc in payload if svc["running"]),
            disabled_count=sum(1 for svc in payload if svc["disabled"]),
        )
        click.echo(
            json_envelope(
                data=payload,
                warnings=warnings,
                status="partial" if warnings else "success",
                reason_code="runtime_status_unavailable" if warnings else None,
            )
        )
        return

    name_width = max(
        [
            18,
            *(len(svc.name) for svc in visible_services),
            *(len(name) for name in disabled_services),
        ]
    )

    # Helper to format service line
    def format_service_line(svc, custom_status=None):
        """Format a service line with status, ports, and description."""
        if svc.name in disabled_services:
            status_marker = "✗"
            status = "Disabled"
            ports = ""
            suffix = "(disabled in phlo.yaml)"
        elif svc.name in running_containers:
            container = running_containers[svc.name]
            state = container.get("status", "")
            status_marker = "✓" if state == "running" else "✗"
            status = "Running" if state == "running" else state.title() or "Unknown"
            port_str = container.get("ports", "")
            external_port = _external_port_for_container(
                port_str, _first_declared_container_port(svc)
            )
            ports = f":{external_port}" if external_port else ""
            suffix = ""
        else:
            status_marker = " "
            status = "Stopped" if runtime_available else "Unknown"
            ports = ""
            suffix = ""

        if custom_status:
            suffix = custom_status

        # Format: "  ✓ service-name    Running    :3000   Description [extra]"
        name_col = f"{svc.name:<{name_width}}"
        status_col = f"{status:<10}"
        ports_col = f"{ports:<7}"
        desc_with_suffix = f"{svc.description} {suffix}".strip()

        return f"  {status_marker} {name_col} {status_col} {ports_col} {desc_with_suffix}"

    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)

    # Display package services
    if package_services or disabled_services:
        click.echo("\nPackage Services (installed):")
        displayed = set()
        for svc in visible_services:
            click.echo(format_service_line(svc))
            displayed.add(svc.name)

        # Show disabled services that aren't in the package list
        for name in sorted(disabled_services):
            if name not in displayed and name in available_services:
                svc = available_services[name]
                click.echo(format_service_line(svc))

    # Display inline custom services
    if inline_services:
        click.echo("\nCustom Services (phlo.yaml):")
        for svc in sorted(inline_services, key=lambda x: x.name):
            click.echo(format_service_line(svc, custom_status="(inline)"))

    logger.info(
        "services_list_completed",
        available_count=len(available_services),
        inline_count=len(inline_services),
        running_count=len(running_containers),
        disabled_count=len(disabled_services),
    )
    click.echo("")
    if warnings:
        raise click.exceptions.Exit(1)
