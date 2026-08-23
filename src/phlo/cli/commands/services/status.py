"""Show service state via the ``phlo services status`` command.

Wraps ``compose ps`` for the project, emitting either a table or JSON; a
failed compose invocation surfaces as a ClickException rather than a
traceback.
"""

import json

import click

from phlo.cli.commands.services.common import parse_service_args, run_compose
from phlo.cli.commands.services.utils import ensure_compose_project, require_container_backend
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.command("status")
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["docker", "podman", "auto"]),
    default=None,
    help="Container backend for this command.",
)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option(
    "--service",
    multiple=True,
    help="Show only specific service(s), e.g. --service postgres,minio or --service postgres.",
)
def status_cmd(backend_name: str | None, output_json: bool, service: tuple[str, ...]):
    """Show status of Phlo infrastructure services.

    Examples:
        phlo services status
        phlo services status --json
    """
    require_container_backend(backend_name)
    phlo_dir = ensure_compose_project()
    project_name = get_project_name()
    services_list = parse_service_args(service)
    logger.info(
        "services_status_requested",
        project_name=project_name,
        phlo_dir=str(phlo_dir),
        service_names=services_list,
    )

    cmd = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        backend_name=backend_name,
    )
    if output_json:
        cmd.extend(["ps", *services_list, "--format", "json"])
    else:
        cmd.extend(
            ["ps", *services_list, "--format", "table {{.Service}}\t{{.Status}}\t{{.Ports}}"]
        )

    result = run_compose(cmd, check=False, capture_output=True)
    if result.returncode != 0:
        logger.warning(
            "services_status_failed",
            project_name=project_name,
            returncode=result.returncode,
        )
        raise click.ClickException("No services running or error checking status.")
    if output_json:
        try:
            services = _parse_compose_json_status(result.stdout or "")
        except json.JSONDecodeError as exc:
            raise click.ClickException("Could not parse container status output.") from exc
        click.echo(json.dumps(services, indent=2))
        logger.info("services_status_succeeded", project_name=project_name, output="json")
        return

    if result.stdout:
        click.echo(result.stdout, nl=False)
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        click.echo("\nNo services are running.")
        click.echo("Run: phlo services start")
    logger.info("services_status_succeeded", project_name=project_name)


def _parse_compose_json_status(stdout: str) -> list[dict[str, object]]:
    """Parse compose ps JSON output across Docker Compose variants."""
    text = stdout.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Compose v2 emits a single JSON array; some builds emit one object
        # per line instead. Fall back to line-delimited parsing.
        parsed = [json.loads(line) for line in text.splitlines() if line.strip()]

    items = [parsed] if isinstance(parsed, dict) else list(parsed)

    services: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        services.append(
            {
                "service": item.get("Service") or item.get("Name") or item.get("service"),
                "name": item.get("Name") or item.get("Container") or item.get("name"),
                "state": item.get("State") or item.get("state"),
                "status": item.get("Status") or item.get("status"),
                "ports": item.get("Publishers") or item.get("Ports") or item.get("ports") or [],
            }
        )
    return services
