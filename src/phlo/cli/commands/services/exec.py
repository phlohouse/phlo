"""Run the ``phlo services exec`` command.

Runs an arbitrary command inside a running service container via the compose
backend. Treated as a mutation: the command passes through mutation
authorization before any container is touched.
"""

from __future__ import annotations

import subprocess

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.utils import ensure_compose_project, require_container_backend
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.command(
    "exec",
    context_settings={"ignore_unknown_options": True},
    help="Run a command inside a running Phlo service container.",
)
@click.argument("service_name")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@click.option("--tty/--no-tty", default=False, show_default=True, help="Allocate a TTY.")
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for this command.",
)
@require_mutation_authorization("services.exec")
def exec_cmd(
    service_name: str,
    command: tuple[str, ...],
    tty: bool,
    backend_name: str | None,
) -> None:
    """Run a command inside a running Phlo service container.

    Examples:
        phlo services exec <service> -- dbt run --select my_model
        phlo services exec trino -- trino --execute "SELECT 1"
        phlo services exec --tty postgres -- psql
    """
    if not command:
        raise click.ClickException("Provide a command after `--`.")

    require_container_backend(backend_name)
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()
    logger.info(
        "services_exec_requested",
        project_name=project_name,
        service_name=service_name,
        tty=tty,
        command_name=command[0],
        arg_count=max(len(command) - 1, 0),
        backend_name=backend_name or "auto",
    )

    cmd = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        backend_name=backend_name,
    )
    cmd.append("exec")
    # Without a caller-side terminal, disable the compose pseudo-TTY so piped
    # stdin and captured output work instead of failing on TTY allocation.
    if not tty:
        cmd.append("-T")
    cmd.append(service_name)
    cmd.extend(command)

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise click.ClickException(
            f"Command exited with status {result.returncode} in service {service_name}."
        )
