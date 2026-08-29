"""Start command for Phlo infrastructure services.

Builds a preflight plan (requested services, profiles, host ports, required
env) and fails before touching the container backend if anything is missing.
Supports compose-backed and native subprocess modes; starting is a mutation
and requires authorization via services.start.
"""

import asyncio
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import yaml

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.common import (
    load_compose_service_names,
    parse_service_args,
    validate_requested_profiles,
)
from phlo.cli.commands.services.planner import StartPreflightPlan, build_start_preflight_plan
from phlo.cli.commands.services.ports import (
    _load_environment,
    _parse_compose_port_spec,
    _resolve_host_port,
)
from phlo.cli.commands.services.utils import (
    _emit_service_lifecycle_events,
    _load_native_state,
    _run_service_hooks,
    _save_native_state,
    _stop_native_processes,
    ensure_phlo_dir,
    expand_service_dependencies,
    get_enabled_disabled_service_names,
    get_profile_service_names,
    require_container_backend,
)
from phlo.cli.infrastructure.command import run_command
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.container_backend import (
    ContainerBackend,
    ServiceStatus,
    select_project_container_backend,
)
from phlo.cli.infrastructure.utils import get_project_name, parse_env_file
from phlo.cli.output import missing_compose_file_error
from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

logger = get_logger(__name__)

READINESS_TIMEOUT_SECONDS = 60
READINESS_POLL_SECONDS = 1


def _declared_healthcheck_services(compose_file: Path, service_names: list[str]) -> set[str]:
    """Return selected services that declare a Compose healthcheck.

    Readiness is deliberately driven by the generated Compose contract rather
    than a backend-specific convention.  ``healthcheck: {disable: true}`` is
    an explicit opt-out and uses the stable-running-state condition instead.
    """
    try:
        compose = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException(
            f"Failed to read readiness contract from {compose_file}: {exc}"
        ) from exc
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return set()
    return {
        name
        for name in service_names
        if isinstance(services.get(name), dict)
        and isinstance(services[name].get("healthcheck"), dict)
        and not services[name]["healthcheck"].get("disable", False)
    }


def _default_compose_service_names(compose_file: Path) -> list[str]:
    """Return default-profile services and dependencies Compose starts without targets."""
    try:
        compose = yaml.safe_load(compose_file.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException(
            f"Failed to read readiness contract from {compose_file}: {exc}"
        ) from exc
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return []
    selected = {
        name
        for name, config in services.items()
        if isinstance(config, dict) and not config.get("profiles")
    }
    pending = list(selected)
    while pending:
        name = pending.pop()
        config = services.get(name)
        if not isinstance(config, dict):
            continue
        dependencies = config.get("depends_on") or {}
        dependency_names = dependencies if isinstance(dependencies, list) else dependencies.keys()
        for dependency in dependency_names:
            if dependency in services and dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return sorted(selected)


def _project_service_statuses(
    backend: ContainerBackend,
    project_name: str,
    *,
    deadline: float,
) -> list[ServiceStatus]:
    """Read readiness states while retaining compatibility with legacy backends.

    Third-party and test backends that predate the explicit readiness method
    still provide the running-state observation used by earlier CLI versions.
    """
    status_reader = getattr(backend, "project_service_statuses", None)
    if callable(status_reader):
        try:
            return status_reader(project_name, deadline=deadline)
        except subprocess.TimeoutExpired:
            return []
    return []


def _format_service_statuses(
    service_names: list[str],
    statuses: list[ServiceStatus],
) -> str:
    """Render one actionable state for every requested service."""
    by_service: dict[str, list[ServiceStatus]] = {}
    for status in statuses:
        by_service.setdefault(status.service, []).append(status)
    rendered: list[str] = []
    for service in sorted(service_names):
        entries = by_service.get(service)
        if not entries:
            rendered.append(f"{service} (not created)")
            continue
        rendered.extend(
            f"{service} (state={entry.state or 'unknown'}"
            + (f", health={entry.health}" if entry.health else "")
            + ")"
            for entry in entries
        )
    return ", ".join(rendered)


def _wait_for_services_ready(
    *,
    backend: ContainerBackend,
    project_name: str,
    compose_file: Path,
    service_names: list[str],
    timeout_seconds: float = READINESS_TIMEOUT_SECONDS,
    poll_seconds: float = READINESS_POLL_SECONDS,
) -> list[str]:
    """Wait for every selected compose service to meet its declared readiness.

    A declared Compose healthcheck must report ``healthy``.  Services without
    one are ready once their container reports ``running``.  No containers are
    stopped on timeout: the final state and log command make partial startup
    inspectable and recoverable.
    """
    healthcheck_services = _declared_healthcheck_services(compose_file, service_names)
    if not callable(getattr(backend, "project_service_statuses", None)):
        # Backends created before the explicit readiness contract retain the
        # previous successful-start behavior until they opt into it.
        return sorted(service_names)
    deadline = time.monotonic() + timeout_seconds
    latest: list[ServiceStatus] = []
    has_observation = False
    while True:
        # Do not replace the last observed state with a no-budget inspection
        # after polling has already reached the readiness deadline.
        if has_observation and time.monotonic() >= deadline:
            rendered = _format_service_statuses(service_names, latest)
            logger.error(
                "services_start_readiness_timeout",
                project_name=project_name,
                timeout_seconds=timeout_seconds,
                services=rendered,
            )
            raise click.ClickException(
                f"services did not become ready within {timeout_seconds:g}s: {rendered}. "
                "Containers were left running for inspection. Run `phlo services list` and "
                "`phlo services logs <service>` for details."
            )
        latest = _project_service_statuses(backend, project_name, deadline=deadline)
        has_observation = True
        by_service: dict[str, list[ServiceStatus]] = {}
        for status in latest:
            by_service.setdefault(status.service, []).append(status)
        ready = True
        for service in service_names:
            entries = by_service.get(service, [])
            if not entries or any(entry.state != "running" for entry in entries):
                ready = False
                break
            if service in healthcheck_services and any(
                entry.health != "healthy" for entry in entries
            ):
                ready = False
                break
        if ready:
            return sorted(service_names)
        if time.monotonic() >= deadline:
            rendered = _format_service_statuses(service_names, latest)
            logger.error(
                "services_start_readiness_timeout",
                project_name=project_name,
                timeout_seconds=timeout_seconds,
                services=rendered,
            )
            raise click.ClickException(
                f"services did not become ready within {timeout_seconds:g}s: {rendered}. "
                "Containers were left running for inspection. Run `phlo services list` and "
                "`phlo services logs <service>` for details."
            )
        time.sleep(poll_seconds)


def _load_native_env_overrides(project_root: Path) -> dict[str, str]:
    """Load project env values for native service subprocesses."""
    env_values: dict[str, str] = {}
    for path in (project_root / ".phlo" / ".env", project_root / ".phlo" / ".env.local"):
        values = parse_env_file(path, strip_quotes=True)
        for key, value in values.items():
            env_values[key.strip()] = value
    return env_values


def _load_disabled_service_names(project_root: Path) -> set[str]:
    """Load disabled service names from project config, tolerating missing/bad files."""
    config_file = project_root / "phlo.yaml"
    if not config_file.exists():
        return set()

    try:
        config = yaml.safe_load(config_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        logger.warning(
            "services_start_config_read_failed",
            config_file=str(config_file),
            exc_info=True,
        )
        return set()

    _, disabled_names = get_enabled_disabled_service_names(
        config if isinstance(config, dict) else {}
    )
    return disabled_names


def _load_project_config(project_root: Path) -> dict[str, Any]:
    """Load project config for service startup checks."""
    config_file = project_root / "phlo.yaml"
    if not config_file.exists():
        return {}

    try:
        config = yaml.safe_load(config_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        logger.warning(
            "services_start_config_read_failed",
            config_file=str(config_file),
            exc_info=True,
        )
        return {}

    return config if isinstance(config, dict) else {}


def _is_host_port_available(port: int) -> bool:
    """Return whether the local host can bind the given TCP port."""
    bind_targets = [(socket.AF_INET, ("0.0.0.0", port))]
    if hasattr(socket, "AF_INET6"):
        bind_targets.append((socket.AF_INET6, ("::", port)))

    for family, address in bind_targets:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                if family == socket.AF_INET6 and hasattr(socket, "IPV6_V6ONLY"):
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(address)
        except AttributeError:
            continue
        except OSError:
            return False
    return True


def _preflight_requested_host_ports(
    *,
    plan: StartPreflightPlan,
) -> None:
    """Fail early when requested stopped services would collide with local host ports."""
    try:
        compose_config = yaml.safe_load(plan.compose_file.read_text()) or {}
    except OSError as exc:
        raise click.ClickException(f"Failed to read {plan.compose_file}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Failed to parse {plan.compose_file}: {exc}") from exc

    services = compose_config.get("services") or {}
    if not isinstance(services, dict):
        return

    selected_services = {
        name: service_config
        for name in plan.service_names
        if isinstance((service_config := services.get(name)), dict)
        and bool(service_config.get("ports"))
    }
    if not selected_services:
        return

    config = _load_project_config(plan.project_root)
    env = _load_environment(plan.phlo_dir, config)
    backend = select_project_container_backend(cli_backend=plan.backend_name)
    running_containers = {
        container.service: {
            "status": container.state,
            "ports": [],
        }
        for container in backend.list_project_containers(plan.project_name)
    }

    conflicts: list[tuple[str, int, str | None]] = []
    invalid_ports: list[tuple[str, str, str | None, str]] = []
    for service_name, service_config in selected_services.items():
        if service_name in running_containers:
            continue

        for port_entry in service_config.get("ports") or []:
            env_var: str | None = None
            host_port: int | None = None

            if isinstance(port_entry, str):
                port_spec = _parse_compose_port_spec(port_entry)
                if not port_spec.container_port.isdigit():
                    continue
                if port_spec.env_var:
                    resolved_value = env.get(port_spec.env_var)
                    if resolved_value and not resolved_value.isdigit():
                        invalid_ports.append(
                            (service_name, str(port_entry), port_spec.env_var, resolved_value)
                        )
                        continue
                elif port_spec.host_port and not port_spec.host_port.isdigit():
                    invalid_ports.append((service_name, str(port_entry), None, port_spec.host_port))
                    continue
                host_port, _, env_var = _resolve_host_port(
                    port_str=port_entry,
                    port_spec=port_spec,
                    service_name=service_name,
                    container_port=int(port_spec.container_port),
                    env=env,
                    running_containers={},
                )
            elif isinstance(port_entry, dict):
                published = port_entry.get("published")
                if published is not None and str(published).isdigit():
                    host_port = int(published)

            if host_port is not None and not _is_host_port_available(host_port):
                conflicts.append((service_name, host_port, env_var))

    if invalid_ports:
        rendered = ", ".join(
            f"{service} -> {value}" + (f" ({env_var})" if env_var else f" in {port_spec}")
            for service, port_spec, env_var, value in invalid_ports
        )
        logger.warning(
            "services_start_invalid_host_ports",
            project_name=plan.project_name,
            invalid_ports=rendered,
        )
        raise click.ClickException(
            "invalid host port value before starting services: "
            f"{rendered}. Use a numeric TCP port in .phlo/.env.local or phlo.yaml env:."
        )

    if not conflicts:
        return

    rendered = ", ".join(
        f"{service} -> {port}" + (f" ({env_var})" if env_var else "")
        for service, port, env_var in conflicts
    )
    logger.warning(
        "services_start_host_port_conflicts",
        project_name=plan.project_name,
        conflicts=rendered,
    )
    raise click.ClickException(
        "host port already in use before starting services: "
        f"{rendered}. Stop the process using the port or override it in .phlo/.env.local."
    )


def _expand_requested_services(
    discovery: ServiceDiscovery,
    service_names: list[str],
) -> list[ServiceDefinition]:
    """Expand explicit service targets with dependencies and setup companions."""
    if not service_names:
        return []

    all_services = discovery.discover()
    unknown_services = [name for name in service_names if name not in all_services]
    if unknown_services:
        raise click.ClickException(f"Unknown service name(s): {', '.join(unknown_services)}")

    requested = [all_services[name] for name in service_names]
    return expand_service_dependencies(discovery, requested)


def _preflight_required_env_vars(
    *,
    phlo_dir: Path,
    project_root: Path,
    services: list[ServiceDefinition],
) -> None:
    """Fail before compose start when selected services are missing required env values."""
    if not services:
        return

    env = _load_environment(phlo_dir, _load_project_config(project_root))
    missing: list[str] = []
    for service in services:
        for env_name, spec in sorted((service.env_vars or {}).items()):
            if not isinstance(spec, dict) or "default" in spec:
                continue
            value = env.get(env_name)
            if value is None or value == "":
                missing.append(f"{service.name}: {env_name}")

    if not missing:
        return

    rendered = ", ".join(missing)
    logger.warning(
        "services_start_required_env_missing",
        missing=rendered,
        service_count=len(services),
    )
    raise click.ClickException(
        "required environment values are missing for selected services: "
        f"{rendered}. Set them in phlo.yaml env: or .phlo/.env.local."
    )


@click.command("start", help="Start Phlo infrastructure services.")
@click.option(
    "-d",
    "--detach/--no-detach",
    default=True,
    help="Run in background",
)
@click.option("--build", is_flag=True, help="Build images before starting")
@click.option(
    "--profile",
    multiple=True,
    help="Enable optional profiles (e.g., observability, api)",
)
@click.option(
    "--service",
    multiple=True,
    help=(
        "Start only specific service(s), e.g. --service postgres,minio "
        "or --service postgres --service minio."
    ),
)
@click.option(
    "--native",
    is_flag=True,
    help="Run services with a native dev command as subprocesses (e.g., phlo-api, Observatory)",
)
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for this command.",
)
@require_mutation_authorization("services.start")
def start_cmd(
    detach: bool,
    build: bool,
    profile: tuple[str, ...],
    service: tuple[str, ...],
    native: bool,
    backend_name: str | None,
):
    """Start Phlo infrastructure services.

    Examples:
        phlo services start
        phlo services start --build
        phlo services start --profile observability
        phlo services start --service postgres
        phlo services start --native
    """
    phlo_dir = ensure_phlo_dir()
    compose_file = phlo_dir / "docker-compose.yml"
    project_name = get_project_name()
    lifecycle_request_id = uuid4().hex
    logger.info(
        "services_start_requested",
        project_name=project_name,
        detach=detach,
        build=build,
        native=native,
        profile_count=len(profile),
        service_args_count=len(service),
    )

    if not compose_file.exists():
        logger.error(
            "services_start_missing_compose_file",
            project_name=project_name,
            compose_file=str(compose_file),
        )
        raise missing_compose_file_error(compose_file.relative_to(Path.cwd()))

    profile = validate_requested_profiles(profile)

    # Parse comma-separated services
    services_list = parse_service_args(service)

    # When --profile is specified without --service, target only profile services
    # This prevents restarting already-running core services
    if profile and not services_list:
        disabled_names = _load_disabled_service_names(Path.cwd())
        services_list = [
            name for name in get_profile_service_names(profile) if name not in disabled_names
        ]
        if not services_list:
            profile_list = ", ".join(profile)
            logger.warning(
                "services_start_profile_resolved_empty",
                project_name=project_name,
                profiles=profile_list,
            )
            raise click.UsageError(f"profile(s) resolve to no services: {profile_list}")
    logger.info(
        "services_start_targets_resolved",
        project_name=project_name,
        service_count=len(services_list),
        service_names=services_list,
    )

    if services_list:
        click.echo(f"Starting services: {', '.join(services_list)}...")
    elif native:
        click.echo(f"Starting {project_name} infrastructure (native dev services enabled)...")
    else:
        click.echo(f"Starting {project_name} infrastructure...")

    discovery = ServiceDiscovery()
    resolved_services: list[ServiceDefinition] = []
    if services_list:
        try:
            resolved_services = _expand_requested_services(discovery, services_list)
        except ValueError as exc:
            logger.warning(
                "services_start_dependency_resolution_failed",
                project_name=project_name,
                service_names=services_list,
                error=str(exc),
            )
            raise click.ClickException(str(exc)) from exc

    _preflight_required_env_vars(
        phlo_dir=phlo_dir,
        project_root=Path.cwd(),
        services=resolved_services,
    )

    # If native dev services are enabled, start Docker services excluding native ones,
    # then start native processes for the excluded services.
    native_service_names: set[str] = set()
    if native:
        from phlo.plugins.compose.native import NativeProcessManager

        project_root = Path.cwd()
        dev_manager = NativeProcessManager(
            project_root, log_dir=project_root / ".phlo" / "native-logs"
        )

        for _, svc in discovery.discover().items():
            if dev_manager.can_run_dev(svc):
                native_service_names.add(svc.name)
        logger.info(
            "services_start_native_capabilities_resolved",
            project_name=project_name,
            native_capable_count=len(native_service_names),
        )

        if not native_service_names:
            logger.warning(
                "services_start_native_unavailable",
                project_name=project_name,
            )
            click.echo("Warning: No services support native mode; starting Docker only.", err=True)
            native = False

    docker_services_list = (
        [service.name for service in resolved_services] if resolved_services else services_list
    )
    if native and not docker_services_list and not profile:
        try:
            compose_config = yaml.safe_load(compose_file.read_text()) or {}
        except OSError as e:
            logger.error(
                "services_start_compose_read_failed",
                project_name=project_name,
                compose_file=str(compose_file),
                exc_info=True,
            )
            raise click.ClickException(f"Failed to read {compose_file}: {e}") from e
        except yaml.YAMLError as e:
            logger.error(
                "services_start_compose_parse_failed",
                project_name=project_name,
                compose_file=str(compose_file),
                exc_info=True,
            )
            raise click.ClickException(f"Failed to parse {compose_file}: {e}") from e
        compose_service_names = list((compose_config.get("services") or {}).keys())
        docker_services_list = [n for n in compose_service_names if n not in native_service_names]

    if native and docker_services_list:
        docker_services_list = [n for n in docker_services_list if n not in native_service_names]

    if native and resolved_services:
        required_docker_services = [
            service.name
            for service in resolved_services
            if service.name not in native_service_names
        ]
        docker_services_list = required_docker_services

    # If the user explicitly requested services and all of them are native-capable,
    # avoid running `docker compose up` with no service args (which would start the entire stack).
    skip_docker_compose = bool(native and services_list and not docker_services_list)
    logger.info(
        "services_start_execution_mode_resolved",
        project_name=project_name,
        skip_docker_compose=skip_docker_compose,
        docker_service_count=len(docker_services_list),
        docker_service_names=docker_services_list,
        native=native,
    )

    docker_service_names: list[str] = []
    if not skip_docker_compose:
        docker_service_names = docker_services_list or load_compose_service_names(compose_file)
        _emit_service_lifecycle_events(
            "pre_start",
            docker_service_names,
            project_name=project_name,
            project_root=Path.cwd(),
            request_id=lifecycle_request_id,
            metadata={"native": False},
        )

    if not skip_docker_compose:
        require_container_backend(backend_name)
        preflight_plan = build_start_preflight_plan(
            phlo_dir=phlo_dir,
            compose_file=compose_file,
            project_root=Path.cwd(),
            project_name=project_name,
            services=resolved_services,
            backend_name=backend_name,
            service_names=docker_service_names,
        )
        _preflight_requested_host_ports(
            plan=preflight_plan,
        )
    elif build:
        logger.warning(
            "services_start_build_ignored_native_only",
            project_name=project_name,
        )
        click.echo("Warning: --build ignored when starting native-only services.", err=True)

    def _stop_docker_services(service_names: set[str]) -> None:
        if not service_names:
            return
        stop_cmd = compose_base_cmd(
            phlo_dir=phlo_dir,
            project_name=project_name,
            profiles=profile,
            backend_name=backend_name,
        )
        stop_cmd.append("stop")
        stop_cmd.extend(sorted(service_names))
        run_command(stop_cmd, check=False, capture_output=False)

    cmd = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        profiles=profile,
        backend_name=backend_name,
    )
    cmd.append("up")

    if detach:
        cmd.append("-d")

    if build:
        cmd.append("--build")

    # Add specific services if specified
    if docker_services_list:
        cmd.extend(docker_services_list)
    if not skip_docker_compose:
        logger.info(
            "services_start_docker_started",
            project_name=project_name,
            detach=detach,
            build=build,
            service_count=len(docker_services_list),
            service_names=docker_services_list,
        )

    try:
        if skip_docker_compose:
            # Skip docker-compose - create a successful result
            result = subprocess.CompletedProcess(args=[], returncode=0)
        else:
            result = run_command(cmd, check=False, capture_output=False)
            if result.returncode != 0:
                logger.error(
                    "services_start_docker_failed",
                    project_name=project_name,
                    returncode=result.returncode,
                    service_count=len(docker_service_names),
                    service_names=docker_service_names,
                )
            else:
                logger.info(
                    "services_start_docker_completed",
                    project_name=project_name,
                    service_count=len(docker_service_names),
                    service_names=docker_service_names,
                )

        if result.returncode == 0:
            if native:
                from phlo.plugins.compose.native import NativeProcessManager

                discovery = ServiceDiscovery()
                project_root = Path.cwd()
                dev_manager = NativeProcessManager(
                    project_root, log_dir=project_root / ".phlo" / "native-logs"
                )

                available = {
                    svc.name: svc
                    for svc in discovery.discover().values()
                    if dev_manager.can_run_dev(svc)
                }

                if services_list:
                    requested = [available[n] for n in services_list if n in available]
                    expanded: dict[str, ServiceDefinition] = {svc.name: svc for svc in requested}
                    queue = list(requested)
                    while queue:
                        svc = queue.pop(0)
                        for dep_name in svc.depends_on:
                            dep = available.get(dep_name)
                            if dep and dep.name not in expanded:
                                expanded[dep.name] = dep
                                queue.append(dep)
                    try:
                        native_to_start = discovery.resolve_dependencies(list(expanded.values()))
                    except ValueError as exc:
                        logger.warning(
                            "services_start_native_dependency_resolution_failed",
                            project_name=project_name,
                            service_names=[svc.name for svc in expanded.values()],
                            error=str(exc),
                        )
                        raise click.ClickException(str(exc)) from exc
                else:
                    native_to_start = [available[n] for n in sorted(available)]
                logger.info(
                    "services_start_native_targets_resolved",
                    project_name=project_name,
                    service_count=len(native_to_start),
                    service_names=[svc.name for svc in native_to_start],
                )

                # Avoid port collisions by ensuring any previously-started Docker containers for the
                # target native services are stopped before launching subprocesses.
                if not skip_docker_compose and native_to_start:
                    _stop_docker_services({svc.name for svc in native_to_start})

                # Avoid port collisions by stopping previously-started native processes for the
                # target services (do not stop unrelated native services).
                _stop_native_processes(project_root, [svc.name for svc in native_to_start])

                click.echo("")
                if native_to_start:
                    click.echo(
                        f"Starting native services: {', '.join(s.name for s in native_to_start)}..."
                    )
                else:
                    click.echo("No native services to start.")

                async def start_native_services():
                    """Start selected native services, returning name -> process metadata."""
                    started: dict[str, dict] = {}
                    env_overrides = {
                        **_load_native_env_overrides(project_root),
                        "PHLO_PROJECT_PATH": str(project_root),
                        "ENV_FILE_PATH": str(project_root / ".phlo" / ".env"),
                    }
                    for svc in native_to_start:
                        _emit_service_lifecycle_events(
                            "pre_start",
                            [svc.name],
                            project_name=project_name,
                            project_root=project_root,
                            request_id=lifecycle_request_id,
                            metadata={"native": True},
                        )
                        click.echo(f"  Starting {svc.name}...")
                        process = await dev_manager.start_service(svc, env_overrides=env_overrides)
                        if process:
                            click.echo(f"    ✓ {svc.name} started (pid {process.pid})")
                            started[svc.name] = {
                                "pid": process.pid,
                                "started_at": time.time(),
                                "log": str(
                                    project_root / ".phlo" / "native-logs" / f"{svc.name}.log"
                                ),
                            }
                            _emit_service_lifecycle_events(
                                "post_start",
                                [svc.name],
                                project_name=project_name,
                                project_root=project_root,
                                request_id=lifecycle_request_id,
                                status="success",
                                metadata={"native": True, "pid": process.pid},
                            )
                        else:
                            click.echo(f"    ✗ {svc.name} failed to start", err=True)
                            _emit_service_lifecycle_events(
                                "post_start",
                                [svc.name],
                                project_name=project_name,
                                project_root=project_root,
                                request_id=lifecycle_request_id,
                                status="failure",
                                metadata={"native": True},
                            )
                    return started

                started = asyncio.run(start_native_services())
                logger.info(
                    "services_start_native_completed",
                    project_name=project_name,
                    requested_count=len(native_to_start),
                    started_count=len(started),
                    failed_count=max(len(native_to_start) - len(started), 0),
                    service_names=[svc.name for svc in native_to_start],
                )
                if started:
                    state = _load_native_state(project_root)
                    state.update(started)
                    _save_native_state(project_root, state)

                if skip_docker_compose:
                    click.echo("")
                    if native_to_start:
                        click.echo(
                            f"Native services started: {', '.join(s.name for s in native_to_start)}"
                        )
                    else:
                        click.echo("No native services started.")

                    if detach or not native_to_start:
                        return

                    def _stop_and_exit(_signum=None, _frame=None) -> None:
                        """Stop native services for this invocation and exit cleanly."""
                        click.echo("\nStopping native services...")
                        _stop_native_processes(project_root, [svc.name for svc in native_to_start])
                        raise SystemExit(0)

                    old_sigterm = signal.signal(signal.SIGTERM, _stop_and_exit)
                    try:
                        click.echo("Press Ctrl+C to stop native services...")
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        _stop_and_exit()
                    finally:
                        signal.signal(signal.SIGTERM, old_sigterm)
                    return

            started_services: list[str] = []
            if not skip_docker_compose:
                # A zero exit from `compose up` only confirms container
                # creation.  Success is withheld until the backend confirms
                # the runtime state required by each Compose service.
                compose_service_names = load_compose_service_names(compose_file)
                if docker_services_list:
                    selected_services = [
                        name for name in docker_services_list if name in compose_service_names
                    ]
                else:
                    selected_services = _default_compose_service_names(compose_file)
                backend = select_project_container_backend(cli_backend=backend_name)
                started_services = _wait_for_services_ready(
                    backend=backend,
                    project_name=project_name,
                    compose_file=compose_file,
                    service_names=selected_services,
                )

            _emit_service_lifecycle_events(
                "post_start",
                started_services,
                project_name=project_name,
                project_root=Path.cwd(),
                request_id=lifecycle_request_id,
                status="success",
                metadata={"native": False},
            )
            _run_service_hooks(
                "post_start",
                started_services,
                project_name=project_name,
                project_root=Path.cwd(),
            )

            click.echo("")
            click.echo("Phlo infrastructure started.")
            if started_services:
                click.echo(f"Services running: {', '.join(sorted(started_services))}")
            logger.info(
                "services_start_completed",
                project_name=project_name,
                started_count=len(started_services),
                started_services=sorted(started_services),
                native=native,
            )
        else:
            _emit_service_lifecycle_events(
                "post_start",
                docker_service_names,
                project_name=project_name,
                project_root=Path.cwd(),
                request_id=lifecycle_request_id,
                status="failure",
                metadata={"native": False, "returncode": result.returncode},
            )
            logger.error(
                "services_start_failed",
                project_name=project_name,
                returncode=result.returncode,
                service_count=len(docker_service_names),
                service_names=docker_service_names,
            )
            raise click.ClickException(
                f"container compose failed (exit {result.returncode}): {' '.join(cmd)}"
            )
    except FileNotFoundError:
        logger.error(
            "services_start_container_backend_not_found",
            project_name=project_name,
            exc_info=True,
        )
        raise click.ClickException(
            "container backend command not found. Install or configure the selected backend."
        ) from None
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error(
            "services_start_unexpected_error",
            project_name=project_name,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise click.ClickException(f"container compose failed unexpectedly: {exc}") from exc
