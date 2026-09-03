"""Production readiness preflight command.

``phlo services preflight`` evaluates the v1 production trust and readiness
contract (ADR 0047) against the generated project and selected services. It is
read-only apart from an explicitly requested ``--output`` report path, which is
written atomically at mode ``0600``. A failed or unavailable required check
exits non-zero and never contacts a container backend.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click

from phlo.cli.commands.services.common import load_compose_service_names
from phlo.cli.commands.services.utils import ensure_phlo_dir
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.infrastructure.utils import get_project_name, parse_env_file
from phlo.logging import get_logger
from phlo.security.production_preflight import (
    ProductionReadinessState,
    run_production_readiness,
)

logger = get_logger(__name__)

_FALSE_STATES = frozenset({ProductionReadinessState.FAILED, ProductionReadinessState.UNAVAILABLE})


def _resolve_environment(phlo_dir: Path, production: bool) -> str:
    """Resolve the environment label: explicit flag wins, else .env, else dev."""
    if production:
        return "production"
    env_file = phlo_dir / ".env"
    value = parse_env_file(env_file).get("PHLO_ENVIRONMENT", "dev").strip().lower()
    return value or "dev"


def _render_report(report) -> None:
    """Render a concise human table for the readiness report."""
    click.echo(f"Production readiness: {'PASS' if report.passed else 'FAIL'}")
    click.echo(f"Environment: {report.environment}")
    click.echo(f"Services: {', '.join(report.services) or '(none)'}")
    for check in report.checks:
        marker = "ok" if check.state in (ProductionReadinessState.PASSED,) else check.state.value
        click.echo(f"  [{marker:>12}] {check.id.value}: {check.message}")


@click.command("preflight")
@click.option(
    "--production",
    is_flag=True,
    help="Evaluate the production readiness posture (defaults from .phlo/.env).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the stable JSON report.")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Persist the JSON report atomically at mode 0600.",
)
def preflight_cmd(production: bool, as_json: bool, output_path: Path | None) -> None:
    """Evaluate production readiness for the generated stack.

    Examples:
        phlo services preflight --production
        phlo services preflight --production --json
        phlo services preflight --production --json --output .phlo/preflight.json
    """
    phlo_dir = ensure_phlo_dir()
    project_root = Path.cwd()
    project_name = get_project_name()
    compose_file = phlo_dir / "docker-compose.yml"
    environment = _resolve_environment(phlo_dir, production)
    service_names = load_compose_service_names(compose_file) if compose_file.exists() else []

    logger.info(
        "services_preflight_requested",
        project_name=project_name,
        environment=environment,
        service_count=len(service_names),
    )

    plan = SimpleNamespace(
        phlo_dir=phlo_dir,
        compose_file=compose_file,
        service_names=service_names,
    )
    report = run_production_readiness(
        plan=plan,
        project_root=project_root,
        environment=environment,
    )

    if output_path is not None:
        write_sensitive_file(output_path, report.to_json())
        click.echo(f"Wrote: {output_path}")

    if as_json:
        click.echo(report.to_json())
    else:
        _render_report(report)

    if not report.passed:
        failed_ids = [check.id.value for check in report.checks if check.state in _FALSE_STATES]
        suffix = f" (report written to {output_path})" if output_path is not None else ""
        raise click.ClickException(
            "production readiness failed; checks: " + ", ".join(failed_ids) + suffix
        )
