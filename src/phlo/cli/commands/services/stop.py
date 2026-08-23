"""Stop command for stopping services.

Stops compose-project services (optionally removing volumes) or native
dev processes, emits service-lifecycle events, and reports containers
left running under the project. Mutation is authorization-gated; a
failed leftover-container check only warns, it never fails the stop.
"""

from pathlib import Path
from uuid import uuid4

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.common import (
    load_compose_service_names,
    parse_service_args,
    run_compose,
)
from phlo.cli.commands.services.utils import (
    _emit_service_lifecycle_events,
    _load_native_state,
    _stop_native_processes,
    ensure_compose_project,
    get_profile_service_names,
    require_container_backend,
)
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.container_backend import select_project_container_backend
from phlo.cli.infrastructure.utils import get_project_name
from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDiscovery

logger = get_logger(__name__)


def _remaining_project_containers(project_name: str, backend_name: str | None) -> list[str]:
    """Return running containers still attached to the compose project."""
    try:
        backend = select_project_container_backend(cli_backend=backend_name)
        return [container.name for container in backend.list_project_containers(project_name)]
    except Exception:
        logger.warning("services_stop_remaining_container_check_failed", exc_info=True)
        return []


@click.command("stop", help="Stop Phlo infrastructure services.")
@click.option("-v", "--volumes", is_flag=True, help="Remove volumes (deletes data)")
@click.option(
    "--native",
    "stop_native",
    is_flag=True,
    help="Stop native dev services started with `phlo services start --native`",
)
@click.option(
    "--profile",
    multiple=True,
    help="Stop optional profile services",
)
@click.option(
    "--service",
    multiple=True,
    help="Stop only specific service(s) (e.g., --service postgres,minio or --service postgres --service minio)",
)
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for this command.",
)
@require_mutation_authorization("services.stop")
def stop_cmd(
    volumes: bool,
    stop_native: bool,
    profile: tuple[str, ...],
    service: tuple[str, ...],
    backend_name: str | None,
):
    """Stop Phlo infrastructure services.

    Examples:
        phlo services stop
        phlo services stop --volumes
        phlo services stop --profile observability
        phlo services stop --service postgres
        phlo services stop --service postgres,minio
    """
    project_root = Path.cwd()
    lifecycle_request_id = uuid4().hex
    logger.info(
        "services_stop_requested",
        project_name=get_project_name(),
        stop_native=stop_native,
        volumes=volumes,
        profile_count=len(profile),
        service_args_count=len(service),
    )
    if stop_native:
        # Parse comma-separated services for native stop.
        native_services_list = parse_service_args(service)
        native_targets = (
            native_services_list
            if native_services_list
            else list(_load_native_state(project_root).keys())
        )
        if native_targets:
            logger.info(
                "services_stop_native_started",
                project_name=get_project_name(),
                service_count=len(native_targets),
                service_names=native_targets,
            )
            _emit_service_lifecycle_events(
                "pre_stop",
                native_targets,
                project_name=get_project_name(),
                project_root=project_root,
                request_id=lifecycle_request_id,
                metadata={"native": True},
            )
        _stop_native_processes(project_root, native_services_list or None)
        if native_targets:
            logger.info(
                "services_stop_native_completed",
                project_name=get_project_name(),
                service_count=len(native_targets),
                service_names=native_targets,
            )
            _emit_service_lifecycle_events(
                "post_stop",
                native_targets,
                project_name=get_project_name(),
                project_root=project_root,
                request_id=lifecycle_request_id,
                status="success",
                metadata={"native": True},
            )

    # If --native was explicitly requested, skip Docker unless --volumes, --profile, or --service also given.
    if stop_native and not volumes and not profile and not service:
        logger.info("services_stop_native_only_completed", project_name=get_project_name())
        click.echo("Stopped native services.")
        return

    require_container_backend(backend_name)
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()

    # Parse comma-separated services
    services_list = parse_service_args(service)

    # When --profile is specified without --service, target only profile services.
    # With --volumes, use compose down for the profile so dependencies and project
    # volumes created by the profile are removed too.
    if profile and not services_list and not volumes:
        services_list = get_profile_service_names(profile)
    logger.info(
        "services_stop_targets_resolved",
        project_name=project_name,
        service_count=len(services_list),
        service_names=services_list,
    )

    if services_list:
        click.echo(f"Stopping services: {', '.join(services_list)}...")
    else:
        click.echo(f"Stopping {project_name} infrastructure...")

    docker_targets = services_list
    if not docker_targets:
        docker_targets = load_compose_service_names(phlo_dir / "docker-compose.yml")
    if docker_targets:
        _emit_service_lifecycle_events(
            "pre_stop",
            docker_targets,
            project_name=project_name,
            project_root=project_root,
            request_id=lifecycle_request_id,
            metadata={"native": False},
        )

    compose_profiles = profile
    # Enable every discovered profile for a full stop: compose only operates
    # on services whose profile is active, so without these flags `down`
    # would leave profile-scoped services running.
    if not services_list and not profile:
        compose_profiles = tuple(sorted(ServiceDiscovery().get_available_profiles()))

    cmd = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        profiles=compose_profiles,
        backend_name=backend_name,
    )

    if services_list:
        # Stop specific services only
        cmd.extend(["stop", *services_list])
    else:
        # Stop all services
        cmd.append("down")
        if volumes:
            cmd.append("-v")
            click.echo("Warning: Removing volumes will delete all data.")
    logger.info(
        "services_stop_docker_started",
        project_name=project_name,
        service_count=len(services_list),
        service_names=services_list,
        volumes=volumes,
    )

    result = run_compose(cmd, check=False, capture_output=False)
    if result.returncode == 0:
        remaining = (
            [] if services_list else _remaining_project_containers(project_name, backend_name)
        )
        if remaining:
            logger.error(
                "services_stop_left_running_containers",
                project_name=project_name,
                remaining_count=len(remaining),
                remaining_containers=remaining,
            )
            raise click.ClickException(
                "container compose completed but containers still running: " + ", ".join(remaining)
            )
        logger.info(
            "services_stop_succeeded",
            project_name=project_name,
            service_count=len(docker_targets),
            service_names=docker_targets,
            volumes=volumes,
        )
        if docker_targets:
            _emit_service_lifecycle_events(
                "post_stop",
                docker_targets,
                project_name=project_name,
                project_root=project_root,
                request_id=lifecycle_request_id,
                status="success",
                metadata={"native": False},
            )
        if services_list:
            click.echo(f"Stopped services: {', '.join(services_list)}")
        else:
            click.echo(f"{project_name} infrastructure stopped.")
    else:
        logger.error(
            "services_stop_failed",
            project_name=project_name,
            returncode=result.returncode,
            service_count=len(docker_targets),
            service_names=docker_targets,
            volumes=volumes,
        )
        if docker_targets:
            _emit_service_lifecycle_events(
                "post_stop",
                docker_targets,
                project_name=project_name,
                project_root=project_root,
                request_id=lifecycle_request_id,
                status="failure",
                metadata={"native": False, "returncode": result.returncode},
            )
        raise click.ClickException(
            f"container compose failed with code {result.returncode}: {' '.join(cmd)}"
        )
