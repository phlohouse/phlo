"""Reset data volumes via the `phlo services reset` command.

Stops compose services and deletes their data volumes after confirmation.
Selective resets resolve and validate each service volume path against the
project's volumes directory before anything is removed.
"""

import re
import shlex
import shutil

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.common import parse_service_args, run_compose
from phlo.cli.commands.services.utils import ensure_compose_project, require_container_backend
from phlo.cli.contract import PhloCommand
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import confirm_action, json_envelope, user_error
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.command("reset", cls=PhloCommand)
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
@click.option(
    "--dry-run", is_flag=True, help="Preview the reset without changing services or files."
)
@click.option("--json", "output_json", is_flag=True, help="Output a structured result.")
@click.option("--non-interactive", is_flag=True, help="Never prompt; require --yes to reset.")
@require_mutation_authorization("services.reset")
def reset_cmd(
    service: tuple[str, ...],
    yes: bool,
    backend_name: str | None,
    dry_run: bool,
    output_json: bool,
    non_interactive: bool,
):
    """Reset Phlo infrastructure by stopping services and deleting volumes.

    This stops all services and removes their data volumes for a clean slate.
    Use --service to selectively reset only specific service volumes. Preview
    the exact scope with --dry-run; scripts must explicitly confirm with --yes.

    Examples:
        phlo services reset --dry-run --json
        phlo services reset --service postgres
        phlo services reset --service postgres,minio --yes --json
    """
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()
    volumes_dir = phlo_dir / "volumes"
    services_list = parse_service_args(service)
    logger.info(
        "services_reset_requested",
        project_name=project_name,
        service_names=services_list,
        dry_run=dry_run,
        skip_confirmation=yes,
    )
    # Validate before stopping containers. Never accept a path as a service name.
    if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name) for name in services_list):
        raise user_error("Invalid service name.", reason_code="invalid_service_name")
    volume_dirs = [volumes_dir / name for name in services_list] if services_list else [volumes_dir]
    data = {
        "project": project_name,
        "project_directory": str(phlo_dir.resolve()),
        "services": services_list or "all",
        "volume_paths": [str(path.absolute()) for path in volume_dirs],
        "container_volume_scope": "anonymous volumes for selected services"
        if services_list
        else "all Compose-managed project volumes",
        "deleted_paths": [],
        "skipped_paths": [],
    }
    next_steps = [{"command": "phlo services start", "when": "Reset completed"}]

    def emit(status, *, errors=None, reason_code=None):
        if output_json:
            click.echo(
                json_envelope(
                    data=data,
                    status=status,
                    errors=errors,
                    reason_code=reason_code,
                    next_steps=next_steps,
                )
            )
        elif errors:
            for error in errors:
                click.echo(error, err=True)

    if not output_json:
        click.echo(f"Project: {project_name} ({phlo_dir.resolve()})")
        click.echo(f"Stop: {', '.join(services_list) if services_list else 'all project services'}")
        click.echo(f"Delete container volumes: {data['container_volume_scope']}")
        for path in data["volume_paths"]:
            click.echo(f"Delete data: {path}")
    if dry_run:
        reset_args = ["phlo", "services", "reset", "--yes"]
        if backend_name:
            reset_args.extend(["--backend", backend_name])
        for name in services_list:
            reset_args.extend(["--service", name])
        next_steps = [
            {
                "command": shlex.join(reset_args),
                "when": f"Approve this deletion scope from {phlo_dir.parent.resolve()}",
            }
        ]
        emit("planned")
        if not output_json:
            click.echo("Dry run complete. No services or files changed.")
        return
    if not confirm_action(
        "Delete the listed data?", yes=yes, non_interactive=non_interactive or output_json
    ):
        emit("cancelled", reason_code="confirmation_declined")
        if not output_json:
            click.echo("Cancelled. No services or files changed.")
        raise click.exceptions.Exit(1)
    require_container_backend(backend_name)
    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name, backend_name=backend_name)
    cmd.extend(["rm", "-f", "-s", "-v", *services_list] if services_list else ["down", "-v"])
    result = run_compose(cmd, check=False, capture_output=True)
    if result.returncode:
        logger.warning(
            "services_reset_stop_failed", project_name=project_name, returncode=result.returncode
        )
        emit(
            "error",
            errors=["Could not stop all target services. Local data was not deleted."],
            reason_code="service_stop_failed",
        )
        raise click.exceptions.Exit(1)

    errors = []
    # Keep deletion inside the project volumes root: selected symlinks and a
    # replaced root are skipped, and rmtree removes nested links without
    # following them to data outside the selected directory.
    for path in volume_dirs:
        try:
            if volumes_dir.is_symlink() or path.is_symlink():
                data["skipped_paths"].append(str(path.absolute()))
                errors.append(f"Skipped symlink: {path}")
            elif path.exists():
                if not path.is_dir():
                    errors.append(f"Expected a volume directory: {path}")
                    data["skipped_paths"].append(str(path.absolute()))
                    continue
                shutil.rmtree(path)
                data["deleted_paths"].append(str(path.absolute()))
            else:
                data["skipped_paths"].append(str(path.absolute()))
        except OSError as exc:
            errors.append(f"Could not delete {path}: {exc}")
    if errors:
        emit("partial", errors=errors, reason_code="volume_deletion_incomplete")
        raise click.exceptions.Exit(1)
    logger.info(
        "services_reset_completed",
        project_name=project_name,
        deleted_count=len(data["deleted_paths"]),
    )
    emit("success")
    if not output_json:
        click.echo(
            f"Reset complete. Deleted {len(data['deleted_paths'])} local volume directories."
        )
        click.echo("Run: phlo services start")
