"""Production readiness preflight command.

``phlo services preflight`` evaluates the v1 production trust and readiness
contract (ADR 0047) against the generated project and selected services. It is
read-only apart from an explicitly requested ``--output`` report path, which is
written atomically at mode ``0600``. A failed or unavailable required check
exits non-zero and never contacts a container backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import click
import yaml

from phlo.cli.commands.services.common import load_compose_service_names
from phlo.cli.commands.services.utils import ensure_phlo_dir
from phlo.cli.contract import PhloCommand
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.infrastructure.utils import get_project_name, parse_env_file
from phlo.cli.output import json_envelope
from phlo.config.layout import project_env_paths
from phlo.config.project import get_environment_targets, load_project_model
from phlo.logging import get_logger
from phlo.rbac.config import RBACConfigLoader
from phlo.rbac.models import RolesConfig
from phlo.security.production_preflight import (
    ProductionReadinessState,
    run_production_readiness,
)

logger = get_logger(__name__)

_FALSE_STATES = frozenset({ProductionReadinessState.FAILED, ProductionReadinessState.UNAVAILABLE})


def _resolve_environment(phlo_dir: Path, production: bool) -> str:
    """Resolve the environment label: explicit flag wins, else project env files, else dev."""
    if production:
        return "production"
    values: dict[str, str] = {}
    for env_file in project_env_paths(phlo_dir):
        values.update(parse_env_file(env_file))
    value = values.get("PHLO_ENVIRONMENT", "dev").strip().lower()
    return value or "dev"


def _render_report(report) -> None:
    """Render a concise human table for the readiness report."""
    click.echo(f"Production readiness: {'PASS' if report.passed else 'FAIL'}")
    click.echo(f"Environment: {report.environment}")
    click.echo(f"Services: {', '.join(report.services) or '(none)'}")
    for check in report.checks:
        marker = "ok" if check.state in (ProductionReadinessState.PASSED,) else check.state.value
        click.echo(f"  [{marker:>12}] {check.id.value}: {check.message}")


@click.command("preflight", cls=PhloCommand)
@click.option(
    "--production",
    is_flag=True,
    help="Evaluate the production readiness posture (defaults from project env files).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the stable JSON report.")
@click.option(
    "--observatory-dev",
    is_flag=True,
    help="Check the local Observatory identity, policy and environment contract.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Persist the JSON report atomically at mode 0600.",
)
def preflight_cmd(
    production: bool, as_json: bool, output_path: Path | None, observatory_dev: bool
) -> None:
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
    if observatory_dev:
        _observatory_dev_preflight(phlo_dir, production, as_json, output_path)
        return
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
        if not as_json:
            click.echo(f"Wrote: {output_path}")

    if as_json:
        failed_ids = [check.id.value for check in report.checks if check.state in _FALSE_STATES]
        click.echo(
            json_envelope(
                data=json.loads(report.to_json()),
                status="success" if report.passed else "error",
                reason_code=None if report.passed else "production_readiness_failed",
                errors=[f"Readiness check failed: {check}" for check in failed_ids],
            )
        )
        if not report.passed:
            raise click.exceptions.Exit(1)
    else:
        _render_report(report)

    if not report.passed:
        failed_ids = [check.id.value for check in report.checks if check.state in _FALSE_STATES]
        suffix = f" (report written to {output_path})" if output_path is not None else ""
        raise click.ClickException(
            "production readiness failed; checks: " + ", ".join(failed_ids) + suffix
        )


def _observatory_dev_preflight(
    phlo_dir: Path, production: bool, as_json: bool, output: Path | None
) -> None:
    """Report setup failures separately, without claiming live dependency readiness."""
    checks = []
    values = {}
    for path in project_env_paths(phlo_dir):
        values.update(parse_env_file(path))
    values.update(os.environ)
    model = load_project_model(phlo_dir.parent)
    environment = values.get("PHLO_ENVIRONMENT", "development").lower()
    checks.append(
        {
            "reason_code": "development_only",
            "passed": not production
            and not model.regulated
            and not model.regulated_mode
            and environment in {"dev", "development", "local", "test"},
            "message": "Use only a development deployment with loopback UI/API ports.",
        }
    )
    compose = yaml.safe_load((phlo_dir / "docker-compose.yml").read_text())
    ports = [
        port
        for name in ("phlo-api", "observatory")
        for port in compose.get("services", {}).get(name, {}).get("ports", [])
    ]
    checks.append(
        {
            "reason_code": "loopback_required",
            "passed": bool(ports)
            and all(
                isinstance(port, str) and port.startswith(("127.0.0.1:", "[::1]:"))
                for port in ports
            ),
            "message": "Publish development UI/API ports on loopback only.",
        }
    )
    auth = model.authentication.model_dump() if model.authentication else {}
    static = auth.get("static") or {}
    checks.append(
        {
            "reason_code": "provider_missing",
            "passed": auth.get("provider") == "static"
            and static.get("enabled") is True
            and static.get("dev_mode") is True,
            "message": "Configure the static development authentication provider via services init --observatory-dev.",
        }
    )
    loader = RBACConfigLoader(phlo_dir)
    for name, load in (("roles", loader.load_roles), ("policies", loader.load_policies)):
        try:
            loaded = load()
            valid = bool(loaded.roles) if isinstance(loaded, RolesConfig) else bool(loaded.policies)
        except (OSError, ValueError):
            valid = False
        checks.append(
            {
                "reason_code": f"authorization_{name}_missing",
                "passed": valid,
                "message": f"Provide valid .phlo/authorization/{name}.yaml.",
            }
        )
    try:
        get_environment_targets(phlo_dir.parent)
        configured = True
    except (OSError, ValueError, TypeError):
        configured = False
    checks.append(
        {
            "reason_code": "environment_mapping_missing",
            "passed": configured,
            "message": "Configure deployed environments and default_environment in phlo.yaml.",
        }
    )
    passed = all(check["passed"] for check in checks)
    report = {
        "passed": passed,
        "profile": "observatory-dev",
        "checks": checks,
        "live_dependencies_verified": False,
    }
    if output:
        write_sensitive_file(output, json.dumps(report))
    if as_json:
        click.echo(json.dumps(report))
    else:
        for check in checks:
            click.echo(
                f"[{'ok' if check['passed'] else 'failed'}] {check['reason_code']}: {check['message']}"
            )
        click.echo("This validates setup only, not live service readiness.")
    if not passed:
        raise click.exceptions.Exit(1)
