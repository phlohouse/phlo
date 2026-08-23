"""Service read models for Observatory.

Builds service status from registry metadata plus live Docker state: the
Docker CLI is tried first, then the daemon socket over HTTP. When several
containers map to one service the highest-ranked status wins, so a healthy
replica outranks but never hides an unhealthy one. Registry loading stays
quiet: no remote fetches or logging hooks on these paths.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import http.client
import importlib.metadata
import importlib.resources
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from typing import Any

import yaml

from phlo.config.env import load_project_env
from phlo_api.observatory_api.observatory_metadata import safe_metadata
from phlo_api.observatory_api.observatory_models import (
    HealthState,
    ServiceStatus,
    ObservatoryExternalLink,
    ObservatoryHealth,
    ObservatoryService,
    ObservatoryServiceConfigEntry,
    ObservatoryServicePort,
)

DOCKER_SOCKET = "/var/run/docker.sock"
DOCKER_PS_TIMEOUT_SECONDS = 10
DOCKER_SOCKET_TIMEOUT_SECONDS = 2
DOCKER_CLI_CANDIDATES = (
    "docker",
    "/opt/homebrew/bin/docker",
    "/usr/local/bin/docker",
)
# When several containers map to one service, the highest-ranked status wins,
# so a healthy replica outranks a stopped one but never hides an unhealthy one.
DOCKER_SERVICE_STATUS_RANK: dict[ServiceStatus, int] = {
    "running": 4,
    "unhealthy": 3,
    "starting": 2,
    "stopped": 1,
    "unknown": 0,
}
ENV_DEFAULT_RE = re.compile(r"^\$\{[^}:]+:-(?P<default>[^}]+)\}$")
ENV_REFERENCE_RE = re.compile(r"^\$\{(?P<name>[^}:]+)(?::-(?P<default>[^}]+))?\}$")
_DOCKER_CLI_DISABLED = False


def get_registry_data() -> dict[str, Any]:
    """Return local registry data without remote fetches or plugin logging hooks."""
    return _load_registry_data_quiet()


def _registry_package_entries() -> dict[str, dict[str, Any]]:
    """Return trusted registry entries keyed by package and friendly aliases."""
    try:
        registry = get_registry_data()
    except Exception:
        return {}

    plugins = registry.get("plugins") if isinstance(registry, Mapping) else None
    if not isinstance(plugins, Mapping):
        return {}

    entries: dict[str, dict[str, Any]] = {}
    for name, payload in plugins.items():
        if not isinstance(payload, Mapping):
            continue
        package = coerce_str(payload.get("package"), "")
        if not package:
            continue
        normalized = dict(payload)
        normalized["name"] = str(name)
        for key in {str(name), package, package.removeprefix("phlo-")}:
            if key:
                entries[key] = normalized
    return entries


def _load_registry_data_quiet() -> dict[str, Any]:
    """Load registry data without invoking logging hooks or remote fetches."""
    try:
        registry_path = importlib.resources.files("phlo.plugins").joinpath("registry_data.json")
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "registry" / "plugins.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def _registry_service_entries(
    entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Return registry entries that should appear before stack discovery."""
    services: dict[str, Mapping[str, Any]] = {}
    for entry in entries.values():
        if entry.get("type") != "service":
            continue
        name = coerce_str(entry.get("name"), "")
        if name:
            services[name] = entry
    return services


def _registry_entry_for_service(
    service_name: str,
    entries: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    candidates = [service_name]
    parts = service_name.split("-")
    candidates.extend("-".join(parts[:index]) for index in range(len(parts) - 1, 0, -1))
    candidates.extend(f"phlo-{candidate}" for candidate in list(candidates))

    for candidate in candidates:
        entry = entries.get(candidate)
        if isinstance(entry, Mapping):
            return entry
    return None


def _package_installed(package: str) -> bool:
    try:
        importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _registry_metadata(
    service_name: str,
    entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    entry = _registry_entry_for_service(service_name, entries)
    if not isinstance(entry, Mapping):
        return {}

    package = coerce_str(entry.get("package"), "")
    installed = _package_installed(package) if package else False
    return {
        "registry_name": coerce_str(entry.get("name"), service_name),
        "package": package,
        "package_version": coerce_str(entry.get("version"), ""),
        "package_installed": installed,
        "installable": not installed,
        "verified": bool(entry.get("verified")),
        "description": coerce_str(entry.get("description"), ""),
        "tags": list(entry.get("tags", [])) if isinstance(entry.get("tags"), list) else [],
    }


def _available_registry_service(
    service_name: str,
    entry: Mapping[str, Any],
) -> ObservatoryService:
    package = coerce_str(entry.get("package"), "")
    description = coerce_str(entry.get("description"), "")
    tags = list(entry.get("tags", [])) if isinstance(entry.get("tags"), list) else []
    return ObservatoryService(
        id=service_name,
        name=service_name,
        kind=coerce_str(entry.get("type"), "service") or "service",
        status="unknown",
        health=ObservatoryHealth(
            state="unknown",
            message="Package is available to install.",
        ),
        definition_state="available",
        runtime_state="unknown",
        in_stack=False,
        backend="registry",
        metadata=safe_metadata(
            {
                "source": "registry",
                "registry_name": service_name,
                "package": package,
                "package_version": coerce_str(entry.get("version"), ""),
                "package_installed": False,
                "installable": True,
                "verified": bool(entry.get("verified")),
                "description": description,
                "tags": tags,
            }
        ),
    )


def coerce_str(value: Any, default: str = "") -> str:
    """Coerce a value to a string, returning the default when it is None."""
    if value is None:
        return default
    return str(value)


def fallback_services() -> list[ObservatoryService]:
    """Return deterministic service data without package-specific imports."""
    return [
        ObservatoryService(
            id="phlo-api",
            name="phlo-api",
            kind="api",
            status="unknown",
            health=ObservatoryHealth(state="unknown", message="Runtime status unavailable"),
            definition_state="configured",
            runtime_state="unknown",
            in_stack=True,
            backend="native",
            impacts=["observatory"],
            metadata={"source": "fallback", "core": True},
        ),
        ObservatoryService(
            id="observatory",
            name="observatory",
            kind="ui",
            status="unknown",
            health=ObservatoryHealth(state="unknown", message="Runtime status unavailable"),
            definition_state="configured",
            runtime_state="unknown",
            in_stack=True,
            backend="native",
            depends_on=["phlo-api"],
            metadata={"source": "fallback", "core": True},
        ),
    ]


def docker_status_from_container(
    container: Mapping[str, Any],
) -> tuple[ServiceStatus, ObservatoryHealth]:
    """Map a Docker container payload to a service status and health summary."""
    state = coerce_str(container.get("State"), "unknown").lower()
    status_text = coerce_str(container.get("Status"), "")
    status_lower = status_text.lower()

    if state == "running" and "(unhealthy)" in status_lower:
        return "unhealthy", ObservatoryHealth(state="error", message=status_text)
    if state == "running" and "starting" in status_lower:
        return "starting", ObservatoryHealth(state="warning", message=status_text)
    if state == "running":
        health: HealthState = "ok" if "(healthy)" in status_lower else "unknown"
        return "running", ObservatoryHealth(state=health, message=status_text or None)
    if state in {"created", "restarting"}:
        return "starting", ObservatoryHealth(state="warning", message=status_text or state)
    if state == "exited" and "exited (0)" in status_lower:
        return "stopped", ObservatoryHealth(state="ok", message=status_text or "Completed")
    if state in {"exited", "dead", "removing"}:
        return "stopped", ObservatoryHealth(state="warning", message=status_text or state)
    return "unknown", ObservatoryHealth(state="unknown", message=status_text or None)


def docker_inspect_container(container_id: str) -> dict[str, Any]:
    """Inspect a container via the Docker CLI, returning an empty dict on any failure."""
    if not container_id:
        return {}
    docker_cli = docker_cli_path()
    if docker_cli is None:
        return {}
    command = [docker_cli, "inspect", container_id]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=DOCKER_PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, list) and payload and isinstance(payload[0], Mapping):
        return dict(payload[0])
    return {}


def docker_runtime_metadata(container: Mapping[str, Any]) -> dict[str, Any]:
    """Extract restart, exit, and health-probe metadata, inspecting the container if needed."""
    inspected = (
        container
        if "RestartCount" in container or isinstance(container.get("State"), Mapping)
        else docker_inspect_container(coerce_str(container.get("ID"), ""))
    )
    if not inspected:
        return {}

    state = inspected.get("State") if isinstance(inspected.get("State"), Mapping) else {}
    health = (
        state.get("Health")
        if isinstance(state, Mapping) and isinstance(state.get("Health"), Mapping)
        else {}
    )
    health_log = health.get("Log") if isinstance(health, Mapping) else None
    recent_health_exits = (
        [
            entry.get("ExitCode")
            for entry in health_log
            if isinstance(entry, Mapping) and entry.get("ExitCode") not in {None, 0}
        ]
        if isinstance(health_log, list)
        else []
    )
    metadata = {
        "restart_count": inspected.get("RestartCount"),
        "started_at": state.get("StartedAt") if isinstance(state, Mapping) else None,
        "finished_at": state.get("FinishedAt") if isinstance(state, Mapping) else None,
        "exit_code": state.get("ExitCode") if isinstance(state, Mapping) else None,
        "oom_killed": state.get("OOMKilled") if isinstance(state, Mapping) else None,
        "health_status": health.get("Status") if isinstance(health, Mapping) else None,
        "recent_health_exit_codes": recent_health_exits,
    }
    return safe_metadata(
        {key: value for key, value in metadata.items() if value is not None and value != ""}
    )


def health_with_runtime_evidence(
    health: ObservatoryHealth,
    metadata: Mapping[str, Any],
) -> ObservatoryHealth:
    """Downgrade container health to warning on recent kills (exit 137) or restarts."""
    restart_count = metadata.get("restart_count")
    recent_exits = metadata.get("recent_health_exit_codes")
    exit_code = metadata.get("exit_code")
    # Exit code 137 means the container was SIGKILLed -- typically OOM-killed
    # by the kernel or Docker -- even when the container has since restarted
    # and reports healthy.
    has_recent_137 = exit_code == 137 or (isinstance(recent_exits, list) and 137 in recent_exits)
    has_restarts = isinstance(restart_count, int) and restart_count > 0
    if has_recent_137:
        return ObservatoryHealth(
            state="warning",
            message=f"{health.message or 'Running'}; recent container kill detected.",
        )
    if has_restarts and health.state == "ok":
        return ObservatoryHealth(
            state="warning",
            message=f"{health.message or 'Running'}; restarted {restart_count} times.",
        )
    return health


def container_labels(container: Mapping[str, Any]) -> dict[str, str]:
    """Parse a container's labels into a dict, accepting mapping or comma-separated forms."""
    labels = container.get("Labels")
    if isinstance(labels, Mapping):
        return {str(key): str(value) for key, value in labels.items()}
    if not isinstance(labels, str) or not labels:
        return {}
    parsed: dict[str, str] = {}
    for item in labels.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    """HTTP connection tunneled over a Unix domain socket."""

    def __init__(self, socket_path: str):
        super().__init__("localhost", timeout=DOCKER_SOCKET_TIMEOUT_SECONDS)
        self.socket_path = socket_path

    def connect(self) -> None:
        """Connect over the Unix socket instead of TCP."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(DOCKER_SOCKET_TIMEOUT_SECONDS)
        sock.connect(self.socket_path)
        self.sock = sock


def docker_socket_candidates() -> list[str]:
    """Return Docker socket paths that exist on common local runtimes."""
    candidates: list[str] = []
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith("unix://"):
        candidates.append(docker_host.removeprefix("unix://"))
    home = Path.home()
    candidates.extend(
        [
            DOCKER_SOCKET,
            str(home / ".docker" / "run" / "docker.sock"),
            str(home / ".colima" / "default" / "docker.sock"),
            str(home / ".colima" / "docker.sock"),
        ]
    )
    seen: set[str] = set()
    existing: list[str] = []
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        if Path(item).exists():
            existing.append(item)
    return existing


def docker_socket_json(path: str, socket_path: str = DOCKER_SOCKET) -> Any:
    """GET a Docker Engine API path over a Unix socket, returning None on any failure."""
    connection = UnixSocketHTTPConnection(socket_path)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status >= 400:
            return None
        body = response.read().decode()
        return json.loads(body) if body else None
    except (OSError, json.JSONDecodeError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def normalize_docker_api_container(container: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a Docker Engine API container payload to the CLI ps field names."""
    names = container.get("Names")
    if isinstance(names, list) and names:
        name = str(names[0]).lstrip("/")
    else:
        name = coerce_str(container.get("Names") or container.get("Name"), "").lstrip("/")
    return {
        "ID": coerce_str(container.get("Id") or container.get("ID"), ""),
        "Names": name,
        "State": coerce_str(container.get("State"), ""),
        "Status": coerce_str(container.get("Status"), ""),
        "Labels": container.get("Labels") if isinstance(container.get("Labels"), Mapping) else {},
    }


def parse_docker_ps_output(output: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON from docker ps into container dicts, skipping bad lines."""
    containers: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            containers.append(dict(parsed))
    return containers


def docker_cli_path() -> str | None:
    """Return the path to a usable Docker CLI binary, or None when absent."""
    for candidate in DOCKER_CLI_CANDIDATES:
        if candidate == "docker":
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
            continue
        if Path(candidate).exists():
            return candidate
    return None


def docker_ps_containers(*filters: str) -> list[dict[str, Any]] | None:
    """List containers via docker ps, returning None when the CLI is unusable or times out."""
    global _DOCKER_CLI_DISABLED
    # One CLI timeout marks the CLI path dead for the lifetime of the process;
    # later calls go straight to the Unix-socket fallback instead of paying the
    # timeout on every request.
    if _DOCKER_CLI_DISABLED:
        return None
    docker_cli = docker_cli_path()
    if docker_cli is None:
        return None
    command = [docker_cli, "ps", "-a"]
    for filter_value in filters:
        command.extend(["--filter", filter_value])
    command.extend(["--format", "{{json .}}"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=DOCKER_PS_TIMEOUT_SECONDS,
        )
    except OSError:
        return None
    except subprocess.TimeoutExpired:
        _DOCKER_CLI_DISABLED = True
        return None

    if result.returncode != 0:
        return None
    return parse_docker_ps_output(result.stdout)


def load_docker_containers() -> list[dict[str, Any]]:
    """Load all containers via the Docker CLI, falling back to the Engine socket API."""
    containers = docker_ps_containers()
    if containers is not None:
        return containers

    for socket_path in docker_socket_candidates():
        payload = docker_socket_json("/containers/json?all=1", socket_path=socket_path)
        if isinstance(payload, list):
            return [
                normalize_docker_api_container(container)
                for container in payload
                if isinstance(container, Mapping)
            ]
    return []


def load_project_docker_containers(project_root: Path | None) -> list[dict[str, Any]]:
    """Load containers belonging to the project's compose project, empty when unknown."""
    compose_project = project_compose_name(project_root)
    if compose_project:
        containers = docker_ps_containers(f"label=com.docker.compose.project={compose_project}")
        if containers is not None:
            return containers
        return [
            container
            for container in load_docker_containers()
            if container_labels(container).get("com.docker.compose.project") == compose_project
        ]
    return []


def project_compose_name(project_root: Path | None) -> str | None:
    """Resolve the compose project name for a Phlo project root."""
    configured = os.environ.get("PHLO_COMPOSE_PROJECT") or os.environ.get("COMPOSE_PROJECT_NAME")
    if configured:
        return configured
    if project_root is None:
        return None

    compose_file = project_root / ".phlo" / "docker-compose.yml"
    if not compose_file.exists():
        return None

    config_file = project_root / "phlo.yaml"
    if config_file.exists():
        try:
            payload = yaml.safe_load(config_file.read_text()) or {}
        except (OSError, yaml.YAMLError):
            payload = {}
        if isinstance(payload, Mapping):
            name = payload.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()

    return project_root.name


def configured_compose_services(project_root: Path) -> set[str]:
    """Return service names declared in the generated project compose file."""
    compose_file = project_root / ".phlo" / "docker-compose.yml"
    if not compose_file.exists():
        return set()
    try:
        payload = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(payload, Mapping):
        return set()
    services = payload.get("services")
    if not isinstance(services, Mapping):
        return set()
    return {str(name) for name in services}


def current_compose_project(
    containers: Sequence[Mapping[str, Any]],
    project_root: Path | None = None,
) -> str | None:
    """Resolve the active compose project name from env, project config, or running containers."""
    configured = os.environ.get("PHLO_COMPOSE_PROJECT") or os.environ.get("COMPOSE_PROJECT_NAME")
    if configured:
        return configured
    if project_root is not None:
        project_name = project_compose_name(project_root)
        if project_name:
            return project_name

    hostname = os.environ.get("HOSTNAME", "")
    if not hostname:
        return None
    # Inside a container, HOSTNAME is that container's id; matching it against
    # the container list identifies which compose project this process runs in.

    for container in containers:
        container_id = coerce_str(container.get("ID") or container.get("Id"), "")
        if container_id and container_id.startswith(hostname):
            labels = container_labels(container)
            project = labels.get("com.docker.compose.project")
            if project:
                return project

    inspected = docker_socket_json(f"/containers/{hostname}/json")
    if isinstance(inspected, Mapping):
        config = inspected.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        if isinstance(labels, Mapping):
            project = labels.get("com.docker.compose.project")
            if project:
                return str(project)
    return None


def compose_service_name(container: Mapping[str, Any]) -> str | None:
    """Derive the compose service name from container labels or Docker's default naming."""
    labels = container_labels(container)
    service_name = labels.get("com.docker.compose.service")
    if service_name:
        return service_name
    name = coerce_str(container.get("Names"), "")
    # Containers without compose labels still follow Docker's default
    # <project>-<service>-<ordinal> naming; peel off the suffix parts.
    if name.endswith("-1") and "-" in name:
        return name.rsplit("-", 2)[-2]
    return None


def service_name_from_container(name: str, service_ids: set[str]) -> str | None:
    """Match a container name to a known service id, preferring the longest id."""
    ordered_service_ids = list(service_ids)
    ordered_service_ids.sort(key=lambda value: len(value), reverse=True)
    for service_id in ordered_service_ids:
        if name == service_id or name.endswith(f"-{service_id}-1"):
            return service_id
    return None


def load_docker_service_statuses(
    service_ids: set[str],
    containers: Sequence[Mapping[str, Any]] | None = None,
    project_root: Path | None = None,
) -> dict[str, tuple[ServiceStatus, ObservatoryHealth]]:
    """Map known service ids to status and health from the project's containers."""
    if not service_ids:
        return {}

    statuses: dict[str, tuple[ServiceStatus, ObservatoryHealth]] = {}
    containers = (
        containers if containers is not None else load_project_docker_containers(project_root)
    )
    compose_project = current_compose_project(containers, project_root)
    if not compose_project:
        return statuses

    for container in containers:
        labels = container_labels(container)
        if labels.get("com.docker.compose.project") != compose_project:
            continue
        name = coerce_str(container.get("Names"), "")
        service_id = compose_service_name(container) or service_name_from_container(
            name, service_ids
        )
        if service_id not in service_ids:
            service_id = service_name_from_container(name, service_ids)
        if service_id is None:
            continue
        status, health = docker_status_from_container(container)
        runtime_metadata = docker_runtime_metadata(container)
        health = health_with_runtime_evidence(health, runtime_metadata)
        current = statuses.get(service_id)
        if (
            current is None
            or DOCKER_SERVICE_STATUS_RANK[status] > DOCKER_SERVICE_STATUS_RANK[current[0]]
        ):
            statuses[service_id] = (status, health)
    return statuses


def load_docker_service_metadata(
    service_ids: set[str],
    containers: Sequence[Mapping[str, Any]] | None = None,
    project_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Map known service ids to runtime metadata from the project's containers."""
    if not service_ids:
        return {}

    metadata_by_service: dict[str, dict[str, Any]] = {}
    containers = (
        containers if containers is not None else load_project_docker_containers(project_root)
    )
    compose_project = current_compose_project(containers, project_root)
    if not compose_project:
        return metadata_by_service

    for container in containers:
        labels = container_labels(container)
        if labels.get("com.docker.compose.project") != compose_project:
            continue
        name = coerce_str(container.get("Names"), "")
        service_id = compose_service_name(container) or service_name_from_container(
            name, service_ids
        )
        if service_id not in service_ids:
            service_id = service_name_from_container(name, service_ids)
        if service_id is None:
            continue
        metadata_by_service[service_id] = docker_runtime_metadata(container)
    return metadata_by_service


def runtime_services_from_containers(
    containers: Sequence[Mapping[str, Any]],
    known_ids: set[str],
    project_root: Path | None = None,
) -> list[ObservatoryService]:
    """Build service entries for running containers that are not in the known set."""
    compose_project = current_compose_project(containers, project_root)
    if not compose_project:
        return []

    services: list[ObservatoryService] = []
    for container in containers:
        labels = container_labels(container)
        if labels.get("com.docker.compose.project") != compose_project:
            continue
        service_id = compose_service_name(container)
        if not service_id or service_id in known_ids:
            continue
        status, health = docker_status_from_container(container)
        runtime_metadata = docker_runtime_metadata(container)
        health = health_with_runtime_evidence(health, runtime_metadata)
        services.append(
            ObservatoryService(
                id=service_id,
                name=service_id,
                kind=labels.get("phlo.service.category", "service"),
                status=status,
                health=health,
                definition_state="configured",
                runtime_state=status,
                in_stack=True,
                backend="docker",
                metadata=safe_metadata(
                    {
                        "source": "docker",
                        "compose_project": compose_project,
                        **runtime_metadata,
                    }
                ),
            )
        )
        known_ids.add(service_id)
    return services


def service_links_from_definition(service: Any) -> list[ObservatoryExternalLink]:
    """Extract external links from a service definition's Traefik rules and published ports."""
    compose = getattr(service, "compose", {}) if service is not None else {}
    labels = compose.get("labels") if isinstance(compose, Mapping) else {}
    ports = compose.get("ports") if isinstance(compose, Mapping) else []
    links: list[ObservatoryExternalLink] = []

    if isinstance(labels, Mapping):
        for key, value in labels.items():
            if str(key).endswith(".rule") and "Host(`" in str(value):
                host = str(value).split("Host(`", 1)[1].split("`)", 1)[0]
                if host and "$" not in host:
                    links.append(
                        ObservatoryExternalLink(label="Open", url=f"http://{host}", kind="app")
                    )

    for port in ports if isinstance(ports, list) else []:
        if not isinstance(port, str) or ":" not in port:
            continue
        published = resolve_env_default(port.rsplit(":", 1)[0])
        if published.isdigit():
            links.append(
                ObservatoryExternalLink(
                    label=f":{published}",
                    url=f"http://localhost:{published}",
                    kind="port",
                )
            )

    return links[:4]


def service_links_from_compose(
    project_root: Path, service_name: str
) -> list[ObservatoryExternalLink]:
    """Extract port links for a service from the generated compose file."""
    compose_file = project_root / ".phlo" / "docker-compose.yml"
    try:
        payload = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    services = payload.get("services") if isinstance(payload, Mapping) else {}
    service = services.get(service_name) if isinstance(services, Mapping) else None
    if not isinstance(service, Mapping):
        return []
    ports = service.get("ports")
    links: list[ObservatoryExternalLink] = []
    for port in ports if isinstance(ports, list) else []:
        if not isinstance(port, str) or ":" not in port:
            continue
        published = resolve_env_default(port.rsplit(":", 1)[0])
        if published.isdigit():
            links.append(
                ObservatoryExternalLink(
                    label=f":{published}",
                    url=f"http://localhost:{published}",
                    kind="port",
                )
            )
    return links[:4]


def merge_service_links(
    *groups: Iterable[ObservatoryExternalLink],
) -> list[ObservatoryExternalLink]:
    """Merge link groups in order, dropping duplicates and capping at four links."""
    links: list[ObservatoryExternalLink] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for link in group:
            key = (link.label, link.url)
            if key in seen:
                continue
            seen.add(key)
            links.append(link)
    return links[:4]


def service_ports_from_definition(service: Any) -> list[ObservatoryServicePort]:
    """Extract published and target ports from a service definition."""
    compose = getattr(service, "compose", {}) if service is not None else {}
    ports = compose.get("ports") if isinstance(compose, Mapping) else []
    exposed: list[ObservatoryServicePort] = []
    for index, port in enumerate(ports if isinstance(ports, list) else []):
        if not isinstance(port, str):
            continue
        if ":" in port:
            published, target = port.rsplit(":", 1)
        else:
            published, target = None, port
        exposed.append(
            ObservatoryServicePort(
                name=f"port-{index + 1}",
                published=resolve_env_default(published) if published else None,
                target=target,
            )
        )
    return exposed


def resolve_env_default(value: str) -> str:
    """Resolve ``${VAR:-default}`` references in a value using the project env file."""
    match = ENV_REFERENCE_RE.match(value)
    if match is not None:
        env_value = load_project_env().get(match.group("name"))
        if env_value:
            return env_value
        default = match.group("default")
        if default is not None:
            return default
    return value


def _local_port_status(port: str | int | None) -> tuple[ServiceStatus, ObservatoryHealth] | None:
    if port is None:
        return None
    try:
        numeric_port = int(port)
    except (TypeError, ValueError):
        return None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        result = sock.connect_ex(("127.0.0.1", numeric_port))

    if result == 0:
        return (
            "running",
            ObservatoryHealth(state="ok", message=f"Listening on localhost:{numeric_port}"),
        )
    return (
        "stopped",
        ObservatoryHealth(state="warning", message=f"Not listening on localhost:{numeric_port}"),
    )


def native_service_override(
    service_name: str,
    status: ServiceStatus,
    health: ObservatoryHealth,
) -> tuple[ServiceStatus, ObservatoryHealth, str | None]:
    """Probe native ports for core services, overriding status when a port listens locally."""
    env = load_project_env()
    candidates: list[tuple[str, str]] = []
    if service_name == "phlo-api":
        candidates.append(("PHLO_API_PORT", env.get("PHLO_API_PORT", "4000")))
    elif service_name == "observatory":
        configured = env.get("OBSERVATORY_PORT", "3001")
        candidates.append(("OBSERVATORY_PORT", configured))
        if configured != "3000":
            candidates.append(("OBSERVATORY_DEV_PORT", "3000"))

    first_stopped: tuple[ServiceStatus, ObservatoryHealth, str | None] | None = None
    for _env_name, port in candidates:
        local_status = _local_port_status(port)
        if local_status is None:
            continue
        local_runtime_status, local_health = local_status
        if local_runtime_status == "running":
            return local_runtime_status, local_health, str(port)
        if first_stopped is None:
            first_stopped = (local_runtime_status, local_health, str(port))

    if first_stopped is not None and status == "unknown":
        return first_stopped
    return status, health, None


def service_config_from_definition(service: Any) -> list[ObservatoryServiceConfigEntry]:
    """Build config entries from a service definition's env vars, masking secrets."""
    env_vars = getattr(service, "env_vars", {}) if service is not None else {}
    if not isinstance(env_vars, Mapping):
        return []

    entries: list[ObservatoryServiceConfigEntry] = []
    for name, config in sorted(env_vars.items()):
        if not isinstance(config, Mapping):
            continue
        secret = bool(config.get("secret"))
        entries.append(
            ObservatoryServiceConfigEntry(
                name=str(name),
                value=None if secret else coerce_str(config.get("default"), "") or None,
                description=coerce_str(config.get("description"), "") or None,
                secret=secret,
            )
        )
    return entries[:12]


def load_services(
    project_root: Path,
    containers: Sequence[Mapping[str, Any]] | None = None,
) -> list[ObservatoryService]:
    """Load services through core discovery, falling back deterministically."""
    try:
        from phlo.plugins.discovery import ServiceDiscovery

        discovered = ServiceDiscovery().discover().values()
    except Exception:
        discovered = []

    services: list[ObservatoryService] = []
    containers = containers if containers is not None else load_docker_containers()
    discovered = list(discovered)
    registry_entries = _registry_package_entries()
    registry_services = _registry_service_entries(registry_entries)
    configured_services = configured_compose_services(project_root)
    runtime_statuses = load_docker_service_statuses(
        {service.name for service in discovered} | set(registry_services),
        containers,
        project_root,
    )
    runtime_metadata_by_service = load_docker_service_metadata(
        {service.name for service in discovered} | set(registry_services),
        containers,
        project_root,
    )
    for service in discovered:
        in_stack = service.name in runtime_statuses
        configured = in_stack or service.name in configured_services
        status, health = runtime_statuses.get(
            service.name,
            ("unknown", ObservatoryHealth(state="unknown", message="Runtime status unavailable")),
        )
        status, health, native_port = native_service_override(service.name, status, health)
        native_running = native_port is not None and status == "running" and not in_stack
        services.append(
            ObservatoryService(
                id=service.name,
                name=service.name,
                kind=service.category or "service",
                status=status,
                health=health,
                definition_state="configured" if configured else "available",
                runtime_state=status,
                in_stack=in_stack or native_running,
                disabled=bool(getattr(service, "disabled", False)),
                profile=coerce_str(service.profile, "") or None,
                backend="docker" if in_stack else ("native" if native_running else "unknown"),
                depends_on=list(service.depends_on or []),
                impacts=[],
                links=merge_service_links(
                    [
                        ObservatoryExternalLink(
                            label=f":{native_port}",
                            url=f"http://localhost:{native_port}",
                            kind="port",
                        )
                    ]
                    if native_port is not None
                    else [],
                    service_links_from_definition(service),
                    service_links_from_compose(project_root, service.name),
                ),
                metadata=safe_metadata(
                    {
                        "default": bool(service.default),
                        "profile": service.profile,
                        "core": bool(getattr(service, "core", False)),
                        "description": getattr(service, "description", None),
                        **_registry_metadata(service.name, registry_entries),
                        **runtime_metadata_by_service.get(service.name, {}),
                    }
                ),
            )
        )

    discovered_names = {service.id for service in services}
    for service_name, entry in registry_services.items():
        if service_name in discovered_names:
            continue
        service = _available_registry_service(service_name, entry)
        status, health = runtime_statuses.get(
            service_name,
            ("unknown", ObservatoryHealth(state="unknown", message="Runtime status unavailable")),
        )
        status, health, native_port = native_service_override(service_name, status, health)
        native_running = native_port is not None and status == "running"
        if service_name in runtime_statuses:
            service = service.model_copy(
                update={
                    "status": status,
                    "health": health,
                    "definition_state": "configured",
                    "runtime_state": status,
                    "in_stack": True,
                    "backend": "docker",
                    "links": merge_service_links(
                        service.links,
                        service_links_from_compose(project_root, service_name),
                    ),
                    "metadata": safe_metadata(
                        {
                            **service.metadata,
                            **runtime_metadata_by_service.get(service_name, {}),
                        }
                    ),
                }
            )
        elif native_port is not None:
            service = service.model_copy(
                update={
                    "status": status,
                    "health": health,
                    "definition_state": "configured",
                    "runtime_state": status,
                    "in_stack": native_running,
                    "backend": "native" if native_running else service.backend,
                    "links": merge_service_links(
                        [
                            ObservatoryExternalLink(
                                label=f":{native_port}",
                                url=f"http://localhost:{native_port}",
                                kind="port",
                            )
                        ],
                        service.links,
                        service_links_from_compose(project_root, service_name),
                    ),
                }
            )
        services.append(service)

    services.extend(
        runtime_services_from_containers(
            containers, {service.id for service in services}, project_root
        )
    )

    return sorted(services, key=lambda item: item.id) if services else fallback_services()
