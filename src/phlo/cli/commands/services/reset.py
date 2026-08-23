"""Reset data volumes via the `phlo services reset` command.

Stops compose services and deletes their data volumes after confirmation.
Selective resets resolve and validate each service volume path against the
project's volumes directory before anything is removed.
"""

import shutil
from pathlib import Path

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.common import parse_service_args, run_compose
from phlo.cli.commands.services.utils import ensure_compose_project, require_container_backend
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.command("reset")
@click.option(
    "--service",
    multiple=True,
    help=(
        "Reset only specific service volume(s), e.g. --service postgres,minio "
        "or --service postgres --service minio."
    ),
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for this command.",
)
@require_mutation_authorization("services.reset")
def reset_cmd(service: tuple[str, ...], yes: bool, backend_name: str | None):
    """Reset Phlo infrastructure by stopping services and deleting volumes.

    This stops all services and removes their data volumes for a clean slate.
    Use --service to selectively reset only specific service volumes.

    Examples:
        phlo services reset                      # Reset everything
        phlo services reset --service postgres   # Reset only postgres
        phlo services reset --service postgres,minio  # Reset multiple
        phlo services reset -y                   # Skip confirmation
    """
    require_container_backend(backend_name)
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()
    volumes_dir = phlo_dir / "volumes"
    volumes_dir_resolved = volumes_dir.resolve()
    logger.info(
        "services_reset_requested",
        project_name=project_name,
        service_args_count=len(service),
        skip_confirmation=yes,
    )

    # Parse comma-separated services
    services_list = parse_service_args(service)

    # Determine what to reset
    def _resolve_volume_dir(service_name: str) -> Path:
        candidate = volumes_dir / service_name
        resolved = candidate.resolve()
        if not resolved.is_relative_to(volumes_dir_resolved):
            raise ValueError(f"Invalid service path: {service_name}")
        return candidate

    if services_list:
        target = f"services: {', '.join(services_list)}"
        try:
            volume_dirs = [_resolve_volume_dir(s) for s in services_list]
        except ValueError as exc:
            logger.warning(
                "services_reset_invalid_service_path",
                project_name=project_name,
                service_names=services_list,
                error=str(exc),
            )
            raise click.ClickException(str(exc)) from exc
    else:
        target = "all services"
        volume_dirs = [volumes_dir] if volumes_dir.exists() else []

    # Confirm
    if not yes:
        click.echo(f"This will stop {target} and DELETE their data volumes.")
        if not click.confirm("Are you sure you want to continue?"):
            logger.info(
                "services_reset_aborted",
                project_name=project_name,
                target=target,
            )
            click.echo("Aborted.")
            return

    # Stop services - selective or all
    if services_list:
        # Stop and remove only specific services
        click.echo(f"Stopping services: {', '.join(services_list)}...")
        cmd = compose_base_cmd(
            phlo_dir=phlo_dir,
            project_name=project_name,
            backend_name=backend_name,
        )
        cmd.extend(
            [
                "rm",
                "-f",  # Force removal
                "-s",  # Stop containers if running
                "-v",  # Remove anonymous volumes
                *services_list,  # Specific services
            ]
        )
    else:
        # Stop all services
        click.echo(f"Stopping {project_name} infrastructure...")
        cmd = compose_base_cmd(
            phlo_dir=phlo_dir,
            project_name=project_name,
            backend_name=backend_name,
        )
        cmd.extend(
            [
                "down",
                "-v",  # Remove Docker volumes
            ]
        )
    logger.info(
        "services_reset_docker_started",
        project_name=project_name,
        target=target,
        service_count=len(services_list),
    )

    result = run_compose(cmd, check=False, capture_output=False)
    if result.returncode != 0:
        logger.warning(
            "services_reset_docker_failed",
            project_name=project_name,
            returncode=result.returncode,
            target=target,
            service_count=len(services_list),
        )
        click.echo(
            f"Warning: container compose command failed with code {result.returncode}",
            err=True,
        )
        click.echo(f"Command: {' '.join(cmd)}", err=True)
    else:
        logger.info(
            "services_reset_docker_completed",
            project_name=project_name,
            target=target,
            service_count=len(services_list),
        )

    # Deletion is confined to .phlo/volumes: symlinks are never followed and
    # each resolved path must stay inside the volumes root, so a hostile
    # service name or mounted link cannot steer rmtree at other files.
    deleted_count = 0
    for vol_dir in volume_dirs:
        if not vol_dir.exists():
            continue
        try:
            if vol_dir.is_symlink():
                click.echo(f"Warning: Skipping symlink {vol_dir}", err=True)
                continue
            resolved = vol_dir.resolve()
            if not resolved.is_relative_to(volumes_dir_resolved):
                click.echo(f"Warning: Skipping unsafe path {vol_dir}", err=True)
                continue
            if vol_dir.is_dir():
                shutil.rmtree(vol_dir)
                deleted_count += 1
                click.echo(f"Deleted: {vol_dir.relative_to(phlo_dir)}")
        except OSError as e:
            logger.warning(
                "services_reset_volume_delete_failed",
                project_name=project_name,
                volume_path=str(vol_dir),
                error=str(e),
            )
            click.echo(f"Warning: Could not delete {vol_dir}: {e}", err=True)

    # Recreate volumes directory if we deleted it entirely
    if not services_list and not volumes_dir.exists():
        volumes_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "services_reset_completed",
        project_name=project_name,
        target=target,
        deleted_count=deleted_count,
        service_count=len(services_list),
    )

    click.echo("")
    if services_list:
        click.echo(f"Reset complete for: {', '.join(services_list)}")
    else:
        click.echo("Full reset complete. All data volumes have been deleted.")
    click.echo("Run 'phlo services start' to start fresh.")
