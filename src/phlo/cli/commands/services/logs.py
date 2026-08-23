"""Logs command for viewing service logs.

A thin pass-through to the container backend's compose logs: service
selection, tailing, time windows, and formatting options map directly
to compose flags. Requires an available container backend and an
initialized compose project before any output is produced.
"""

import click

from phlo.cli.commands.services.common import parse_service_args, run_compose
from phlo.cli.commands.services.utils import ensure_compose_project, require_container_backend
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.command("logs", help="View logs from Phlo infrastructure services.")
@click.argument("services", nargs=-1)
@click.option(
    "-s",
    "--service",
    "--package",
    "service_options",
    multiple=True,
    help="Service/package to include. Repeat or use commas to select several.",
)
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
@click.option(
    "-n",
    "--tail",
    "--lines",
    "tail",
    default=100,
    type=click.IntRange(min=0),
    show_default=True,
    help="Number of recent lines to show before streaming.",
)
@click.option("--since", help="Show logs since a timestamp or duration supported by Compose.")
@click.option("--until", help="Show logs before a timestamp or duration supported by Compose.")
@click.option("--timestamps", is_flag=True, help="Show log timestamps.")
@click.option("--no-color", is_flag=True, help="Disable colored log output where supported.")
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for this command.",
)
def logs_cmd(
    services: tuple[str, ...],
    service_options: tuple[str, ...],
    follow: bool,
    tail: int,
    since: str | None,
    until: str | None,
    timestamps: bool,
    no_color: bool,
    backend_name: str | None,
):
    """View logs from Phlo infrastructure services.

    Examples:
        phlo services logs
        phlo services logs dagster trino
        phlo logs --service dagster --service trino --follow --lines 200
        phlo logs --backend podman --since 10m postgres
    """
    require_container_backend(backend_name)
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()
    selected_services = parse_service_args((*service_options, *services))
    logger.info(
        "services_logs_requested",
        project_name=project_name,
        service_names=selected_services,
        follow=follow,
        tail=tail,
        since=since,
        until=until,
        timestamps=timestamps,
    )

    cmd = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        backend_name=backend_name,
    )
    cmd.extend(["logs", "--tail", str(tail)])
    if since:
        cmd.extend(["--since", since])
    if until:
        cmd.extend(["--until", until])
    if timestamps:
        cmd.append("--timestamps")
    if no_color:
        cmd.append("--no-color")

    if follow:
        cmd.append("-f")

    cmd.extend(selected_services)

    try:
        result = run_compose(cmd, check=False, capture_output=False)
        if result.returncode != 0:
            logger.warning(
                "services_logs_failed",
                project_name=project_name,
                service_names=selected_services,
                returncode=result.returncode,
            )
            exc = click.ClickException(
                f"container compose failed with code {result.returncode}: {' '.join(cmd)}"
            )
            exc.exit_code = result.returncode
            raise exc
        logger.info(
            "services_logs_completed",
            project_name=project_name,
            service_names=selected_services,
        )
    except KeyboardInterrupt:
        logger.warning(
            "services_logs_interrupted",
            project_name=project_name,
            service_names=selected_services,
        )
