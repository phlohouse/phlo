"""Ports command for showing service port mappings.

Resolves effective host ports per service by preferring live Docker
container mappings, then compose-configured ports, then env defaults, and
augments them with Traefik routes when the proxy is running. Detects
cross-service host-port conflicts and reports table or JSON output.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

from phlo.cli.commands.services.utils import _get_env_overrides, get_enabled_disabled_service_names
from phlo.cli.contract import PhloCommand
from phlo.cli.infrastructure.container_backend import select_project_container_backend
from phlo.cli.infrastructure.utils import get_project_name, parse_env_file
from phlo.cli.output import json_envelope, missing_phlo_project_error
from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

logger = get_logger(__name__)

PORT_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}:(\d+)")
DEFAULT_PORT_PATTERN = re.compile(r"\$\{([^}:]+):-(\d+)\}")


@dataclass
class PortMapping:
    """Represents a resolved port mapping for a service."""

    service: str
    host_port: int
    container_port: int
    source: str
    status: str
    env_var: str | None = None
    url: str | None = None


@dataclass
class ComposePortSpec:
    """Represents a parsed compose port mapping."""

    env_var: str | None
    host_port: str | None
    container_port: str


@dataclass
class TraefikContext:
    """Resolved Traefik route context for URL generation."""

    domain: str
    host_port: int


def _parse_compose_port(port_str: str) -> tuple[str | None, str]:
    """Parse a compose port string into (env_var, container_port).

    Format: "${VAR:-default}:container" or "host:container"
    """
    spec = _parse_compose_port_spec(port_str)
    return (spec.env_var, spec.container_port)


def _parse_compose_port_spec(port_str: str) -> ComposePortSpec:
    """Parse a compose port string into its env/literal host and container parts."""
    normalized = port_str.strip().strip("\"'")
    match = PORT_PATTERN.match(normalized)
    if match:
        return ComposePortSpec(
            env_var=match.group(1),
            host_port=match.group(2),
            container_port=match.group(3),
        )

    if ":" in normalized:
        host_part, container_part = normalized.rsplit(":", 1)
        return ComposePortSpec(
            env_var=None,
            host_port=host_part.rsplit(":", 1)[-1],
            container_port=container_part.split("/", 1)[0],
        )

    return ComposePortSpec(env_var=None, host_port=None, container_port=normalized)


def _resolve_env_var(env_var: str | None, env: dict[str, str]) -> str | None:
    """Resolve an environment variable from the loaded environment."""
    if env_var is None:
        return None
    return env.get(env_var)


def _load_environment(phlo_dir: Path, config: dict[str, Any]) -> dict[str, str]:
    """Load effective compose environment with standard Phlo precedence.

    Precedence, lowest to highest: `.phlo/.env`, `.phlo/.env.local`,
    `phlo.yaml` env overrides, then the current process environment.
    """
    env: dict[str, str] = {}

    env_file = phlo_dir / ".env"
    env_local_file = phlo_dir / ".env.local"

    for file_path in [env_file, env_local_file]:
        if file_path.exists():
            parsed = parse_env_file(file_path)
            env.update(parsed)

    env.update({k: str(v) for k, v in _get_env_overrides(config).items() if isinstance(k, str)})
    env.update(os.environ)

    return env


def _parse_ports_string(ports_str: str) -> list[dict[str, str]]:
    """Parse backend port output into structured port mappings."""
    mappings: list[dict[str, str]] = []
    if not ports_str:
        return mappings
    for port_entry in ports_str.split(", "):
        if "->" not in port_entry:
            continue
        host_part, container_part = port_entry.split("->", 1)
        host_ip = host_part.rsplit(":", 1)[0] if ":" in host_part else "0.0.0.0"
        host_port = host_part.rsplit(":", 1)[-1] if ":" in host_part else host_part
        mappings.append(
            {
                "host_port": host_port,
                "host_ip": host_ip,
                "container_port": container_part,
            }
        )
    return mappings


def _get_running_container_ports(
    project_name: str,
    backend_name: str | None = None,
) -> dict[str, dict]:
    """Get published ports from running containers."""
    try:
        containers = {}
        backend = select_project_container_backend(cli_backend=backend_name)
        for container in backend.list_project_containers(project_name):
            containers[container.service] = {
                "status": container.state,
                "ports": _parse_ports_string(container.ports),
            }
        return containers
    except Exception:
        logger.warning("container_ports_lookup_failed", exc_info=True)
        return {}


def _get_runtime_host_port(
    running_containers: dict[str, Any],
    service_name: str,
    container_port: int,
) -> int | None:
    """Return the live host port for a running container port mapping, if present."""
    container_info = running_containers.get(service_name, {})
    for port_mapping in container_info.get("ports", []):
        container_value = str(port_mapping.get("container_port", "")).split("/", 1)[0]
        if container_value != str(container_port):
            continue
        host_port = port_mapping.get("host_port")
        if host_port and str(host_port).isdigit():
            return int(host_port)
    return None


def _get_default_host_port(port_str: str, port_spec: ComposePortSpec) -> int | None:
    """Resolve a configured host port from a compose mapping when no runtime mapping exists."""
    if port_spec.host_port and port_spec.host_port.isdigit():
        return int(port_spec.host_port)

    if port_spec.env_var:
        default_match = DEFAULT_PORT_PATTERN.search(port_str)
        if default_match:
            return int(default_match.group(2))

    return None


def _resolve_host_port(
    *,
    port_str: str,
    port_spec: ComposePortSpec,
    service_name: str,
    container_port: int,
    env: dict[str, str],
    running_containers: dict[str, Any],
) -> tuple[int | None, str, str | None]:
    """Resolve the effective host port for a service/container port pair.

    Precedence: a live container mapping wins over an env-resolved value,
    which wins over the statically declared compose port. The returned source
    records which layer produced the result.
    """
    resolved_host_port: int | None = None
    source = "default"
    resolved_env_var: str | None = None

    resolved_host_port = _get_runtime_host_port(running_containers, service_name, container_port)
    if resolved_host_port is not None:
        return resolved_host_port, "runtime", None

    if port_spec.env_var:
        resolved_value = _resolve_env_var(port_spec.env_var, env)
        if resolved_value and resolved_value.isdigit():
            return int(resolved_value), "env", port_spec.env_var

    resolved_host_port = _get_default_host_port(port_str, port_spec)
    if resolved_host_port is not None and port_spec.env_var is None and port_spec.host_port:
        source = "compose"

    return resolved_host_port, source, resolved_env_var


def _get_active_traefik_context(
    services: dict[str, ServiceDefinition],
    env: dict[str, str],
    running_containers: dict[str, Any],
    disabled_services: set[str],
    service_overrides: dict[str, Any],
) -> TraefikContext | None:
    """Return Traefik routing context when the proxy is available and running."""
    traefik_service = services.get("traefik")
    if traefik_service is None or "traefik" in disabled_services:
        return None

    if "traefik" not in running_containers:
        return None

    traefik_override = service_overrides.get("traefik", {})
    compose_ports = traefik_service.compose.get("ports", [])
    if isinstance(traefik_override, dict) and traefik_override.get("ports"):
        compose_ports = traefik_override["ports"]

    for port_str in compose_ports:
        port_spec = _parse_compose_port_spec(port_str)
        if port_spec.container_port != "80":
            continue
        host_port, _, _ = _resolve_host_port(
            port_str=port_str,
            port_spec=port_spec,
            service_name="traefik",
            container_port=80,
            env=env,
            running_containers=running_containers,
        )
        if host_port is not None:
            return TraefikContext(
                domain=env.get("TRAEFIK_DOMAIN", "phlo.localhost"),
                host_port=host_port,
            )

    return None


def _get_traefik_routes(
    service: ServiceDefinition,
    traefik: TraefikContext | None,
) -> dict[str, str]:
    """Extract Traefik routes from service labels. Returns {container_port: url}."""
    routes: dict[str, str] = {}
    if traefik is None:
        return routes

    labels = service.compose.get("labels", {})
    if not labels:
        return routes

    if labels.get("traefik.enable") != "true":
        return routes

    router_rule_pattern = re.compile(r"Host\(`([^`]+)`\)")

    router_hostnames: dict[str, str] = {}
    router_services: dict[str, str] = {}
    service_ports: dict[str, str] = {}

    for key, value in labels.items():
        key_str = str(key)

        if key_str.startswith("traefik.http.routers.") and ".rule" in key_str:
            router_name = key_str.replace("traefik.http.routers.", "").replace(".rule", "")
            match = router_rule_pattern.search(str(value))
            if match:
                hostname = match.group(1)
                hostname = hostname.replace(
                    "${TRAEFIK_DOMAIN:-phlo.localhost}",
                    traefik.domain,
                )
                router_hostnames[router_name] = hostname

        if key_str.startswith("traefik.http.routers.") and ".service" in key_str:
            router_name = key_str.replace("traefik.http.routers.", "").replace(".service", "")
            router_services[router_name] = str(value)

        if key_str.startswith("traefik.http.services.") and ".loadbalancer.server.port" in key_str:
            service_name = key_str.replace("traefik.http.services.", "").replace(
                ".loadbalancer.server.port", ""
            )
            service_ports[service_name] = str(value)

    for router_name, hostname in router_hostnames.items():
        url = (
            f"http://{hostname}"
            if traefik.host_port == 80
            else f"http://{hostname}:{traefik.host_port}"
        )

        traefik_svc_name = router_services.get(router_name, router_name)
        port = service_ports.get(traefik_svc_name)
        if port:
            routes[port] = url
            continue

        if router_services.get(router_name) == "api@internal":
            routes["80"] = url

    return routes


def _get_service_routes(
    services: dict[str, ServiceDefinition],
    traefik: TraefikContext | None,
) -> dict[str, dict[str, str]]:
    """Get all Traefik routes indexed by service name."""
    service_routes: dict[str, dict[str, str]] = {}

    for svc in services.values():
        routes = _get_traefik_routes(svc, traefik)
        if routes:
            service_routes[svc.name] = routes

    return service_routes


def _get_service_ports(
    service: ServiceDefinition,
    env: dict[str, str],
    running_containers: dict[str, Any],
    show_all: bool,
    service_override: dict[str, Any] | None = None,
    service_routes: dict[str, dict[str, str]] | None = None,
) -> list[PortMapping]:
    """Get port mappings for a service."""
    ports: list[PortMapping] = []
    compose_ports = service.compose.get("ports", [])
    if isinstance(service_override, dict) and service_override.get("ports"):
        compose_ports = service_override["ports"]

    if not compose_ports:
        return ports

    is_running = service.name in running_containers
    if not is_running and not show_all:
        return ports

    routes = service_routes.get(service.name, {}) if service_routes else {}

    for port_str in compose_ports:
        port_spec = _parse_compose_port_spec(port_str)
        container_port = int(port_spec.container_port)
        resolved_host_port, source, resolved_env_var = _resolve_host_port(
            port_str=port_str,
            port_spec=port_spec,
            service_name=service.name,
            container_port=container_port,
            env=env,
            running_containers=running_containers,
        )

        if resolved_host_port is None:
            continue

        status = "Running" if is_running else "Stopped"

        url = routes.get(str(container_port))

        ports.append(
            PortMapping(
                service=service.name,
                host_port=resolved_host_port,
                container_port=container_port,
                source=source,
                status=status,
                env_var=resolved_env_var,
                url=url,
            )
        )

    return ports


def _detect_conflicts(port_mappings: list[PortMapping]) -> list[tuple[str, str, int]]:
    """Detect port conflicts. Returns list of (service1, service2, port) tuples."""
    host_port_to_services: dict[int, list[str]] = {}
    for pm in port_mappings:
        if pm.status != "Running":
            continue
        if pm.host_port not in host_port_to_services:
            host_port_to_services[pm.host_port] = []
        if pm.service not in host_port_to_services[pm.host_port]:
            host_port_to_services[pm.host_port].append(pm.service)

    conflicts = []
    for port, services in host_port_to_services.items():
        if len(services) > 1:
            for i in range(len(services)):
                for j in range(i + 1, len(services)):
                    conflicts.append((services[i], services[j], port))
    return conflicts


def _format_table(port_mappings: list[PortMapping], conflicts: list[tuple[str, str, int]]) -> None:
    """Format and print the port table."""
    if not port_mappings:
        click.echo("No port mappings found.")
        return

    conflict_ports = {c[2] for c in conflicts}

    has_urls = any(pm.url for pm in port_mappings)

    if has_urls:
        header = f"{'Service':<20} {'Host Port':<12} {'Container Port':<16} {'URL':<35} {'Source':<10} {'Status':<10}"
        separator = "-" * 105
    else:
        header = f"{'Service':<20} {'Host Port':<12} {'Container Port':<16} {'Source':<10} {'Status':<10}"
        separator = "-" * 70

    click.echo(header)
    click.echo(separator)

    for pm in sorted(port_mappings, key=lambda x: x.service):
        prefix = "⚠ " if pm.host_port in conflict_ports else "  "
        if has_urls:
            url_str = pm.url or ""
            row = (
                f"{prefix}{pm.service:<18} "
                f"{pm.host_port:<12} "
                f"{pm.container_port:<16} "
                f"{url_str:<35} "
                f"{pm.source:<10} "
                f"{pm.status:<10}"
            )
        else:
            row = (
                f"{prefix}{pm.service:<18} "
                f"{pm.host_port:<12} "
                f"{pm.container_port:<16} "
                f"{pm.source:<10} "
                f"{pm.status:<10}"
            )
        click.echo(row)

    if conflicts:
        click.echo("")
        for s1, s2, port in conflicts:
            click.echo(f"⚠ Port conflict: {s1} and {s2} both map to host port {port}")


def _format_json(port_mappings: list[PortMapping]) -> None:
    """Format and print JSON output."""
    output = []
    for pm in port_mappings:
        output.append(
            {
                "service": pm.service,
                "host_port": pm.host_port,
                "container_port": pm.container_port,
                "source": pm.source,
                "status": pm.status.lower(),
                "env_var": pm.env_var,
                "url": pm.url,
            }
        )
    click.echo(json_envelope(data=output))


@click.command("ports", cls=PhloCommand)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--all", "show_all", is_flag=True, help="Include stopped services with defaults")
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for runtime status.",
)
def ports_cmd(output_json: bool, show_all: bool, backend_name: str | None):
    """Show port mappings for all services.

    Displays host port, container port, source (default/env/runtime), and status.

    Examples:
        phlo services ports
        phlo services ports --json
        phlo services ports --all
    """
    logger.info(
        "services_ports_requested",
        output_json=output_json,
        show_all=show_all,
    )

    phlo_dir = Path.cwd() / ".phlo"
    if not phlo_dir.exists():
        raise missing_phlo_project_error()

    config_file = Path.cwd() / "phlo.yaml"
    existing_config: dict = {}
    if config_file.exists():
        try:
            with config_file.open() as f:
                existing_config = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.error("config_read_failed", exc_info=True)
            raise click.ClickException(f"Failed to read {config_file}.") from exc

    _, disabled_services = get_enabled_disabled_service_names(existing_config)
    service_overrides = existing_config.get("services", {})

    env = _load_environment(phlo_dir, existing_config)

    try:
        discovery = ServiceDiscovery()
        available_services = discovery.discover()
    except Exception as exc:
        logger.error("services_discovery_failed", exc_info=True)
        raise click.ClickException(
            "Failed to discover services. Verify service plugins are installed."
        ) from exc

    project_name = get_project_name()
    running_containers = _get_running_container_ports(project_name, backend_name)

    traefik = _get_active_traefik_context(
        available_services,
        env,
        running_containers,
        disabled_services,
        service_overrides if isinstance(service_overrides, dict) else {},
    )
    service_routes = _get_service_routes(available_services, traefik)

    port_mappings: list[PortMapping] = []

    for svc in available_services.values():
        if svc.name in disabled_services:
            continue
        service_override = (
            service_overrides.get(svc.name, {}) if isinstance(service_overrides, dict) else {}
        )
        ports = _get_service_ports(
            svc,
            env,
            running_containers,
            show_all,
            service_override=service_override if isinstance(service_override, dict) else None,
            service_routes=service_routes,
        )
        port_mappings.extend(ports)

    conflicts = _detect_conflicts(port_mappings)

    if output_json:
        _format_json(port_mappings)
    else:
        _format_table(port_mappings, conflicts)

    logger.info(
        "services_ports_completed",
        total_mappings=len(port_mappings),
        conflicts=len(conflicts),
    )
