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
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

import click
import httpx
import yaml

from phlo.cli.commands.services.common import load_compose_service_names
from phlo.cli.commands.services.utils import ensure_phlo_dir
from phlo.cli.contract import PhloCommand
from phlo.cli.infrastructure.command import run_command
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.infrastructure.utils import get_project_name, parse_env_file
from phlo.cli.output import json_envelope
from phlo.config.layout import project_env_paths
from phlo.config.process import get_process_settings
from phlo.config.project import get_default_environment, get_environment_targets, load_project_model
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


def prepare_observatory_start(
    *,
    project: dict,
    opt_in: bool,
    native: bool,
    profile: tuple[str, ...],
    service: tuple[str, ...],
    detach: bool,
    backend_name: str | None,
) -> tuple[tuple[str, ...], tuple[str, str, str] | None]:
    """Activate the saved explicit local profile, never infer identity from missing auth."""
    authentication = project.get("authentication") or {}
    configured_observatory = project.get("services", {}).get("observatory", {}).get(
        "enabled"
    ) is True and not (native or profile or service)
    if configured_observatory:
        service = tuple(
            dict.fromkeys(
                (
                    "observatory",
                    "dagster",
                    "nessie",
                    *project.get("services", {}).get("enabled", []),
                )
            )
        )
    persisted = (
        authentication.get("provider") == "static"
        and (authentication.get("static") or {}).get("dev_mode") is True
        and configured_observatory
    )
    if not (opt_in or persisted):
        if (
            authentication.get("provider")
            and configured_observatory
            and get_process_settings().phlo_authz_token
            and _resolve_environment(Path.cwd() / ".phlo", False) != "production"
        ):
            return service, (get_default_environment(), *_loopback_observatory_urls(backend_name))
        return service, None
    if native or profile or (service and not configured_observatory) or not detach:
        raise click.UsageError(
            "Local Observatory onboarding uses detached containers; omit --native, --profile, --service and --no-detach."
        )
    return service or ("observatory", "dagster", "nessie"), prepare_observatory_development(
        backend_name
    )


def prepare_observatory_development(backend_name: str | None) -> tuple[str, str, str]:
    """Reuse init only for a fresh stack, then validate the effective port bindings."""
    from phlo.cli.commands.services.init import init_cmd

    root = Path.cwd()
    phlo_dir = root / ".phlo"
    if not (phlo_dir / "docker-compose.yml").exists():
        click.get_current_context().invoke(init_cmd, observatory_dev=True, no_dev=True)
    _observatory_dev_preflight(phlo_dir, False, False, None)
    targets = get_environment_targets(root)
    if len(targets) != 1:
        raise click.ClickException(
            "Local onboarding requires one actual local target. Review phlo.yaml environments; use ordinary services start for a configured multi-environment deployment."
        )
    env, target = next(iter(targets.items()))
    if target.compose_project != get_project_name():
        raise click.ClickException(
            "The local target compose_project must match this generated project. Review phlo.yaml rather than starting another environment."
        )
    return env, *_loopback_observatory_urls(backend_name)


def _loopback_observatory_urls(backend_name: str | None) -> tuple[str, str]:
    """Resolve merged port bindings without forwarding credentials to arbitrary endpoints."""
    phlo_dir = Path.cwd() / ".phlo"
    command = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=get_project_name(),
        backend_name=backend_name,
        profiles=("*",),
    )
    result = run_command([*command, "config", "--format", "json"], check=False)
    if result.returncode:
        raise click.ClickException(
            "Cannot resolve generated Compose configuration. Run services preflight and review overrides."
        )
    services = json.loads(result.stdout)["services"]
    urls = []
    for service, port in (("phlo-api", 4000), ("observatory", 3000)):
        bindings = services.get(service, {}).get("ports", [])
        if not bindings or any(
            binding.get("host_ip") not in {"127.0.0.1", "::1"} for binding in bindings
        ):
            raise click.ClickException(
                "Effective UI/API ports must bind only to loopback. Review Compose override layers."
            )
        selected = [binding for binding in bindings if binding.get("target") == port]
        if len(selected) != 1 or not str(selected[0].get("published", "")).isdigit():
            raise click.ClickException("Local UI/API ports must have one explicit published port.")
        host = "[::1]" if selected[0]["host_ip"] == "::1" else "127.0.0.1"
        urls.append(f"http://{host}:{selected[0]['published']}")
    (phlo_dir / "observatory-ready.json").unlink(missing_ok=True)
    return urls[0], urls[1]


def report_observatory_start(
    phlo_dir: Path,
    urls: tuple[str, str, str] | None,
    backend_name: str | None,
    started_observatory: bool,
) -> None:
    """Never confuse successful container startup with authenticated usability."""
    if urls:
        wait_observatory_development(phlo_dir, *urls, backend_name=backend_name)
    elif started_observatory:
        write_sensitive_file(
            phlo_dir / "observatory-ready.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "verification_required",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "browser_login_verified": False,
                }
            ),
        )
        click.echo(
            "Observatory infrastructure started; authenticated usability is NOT verified. Sign in through your configured ingress and check mandatory /api/v1/readiness capabilities. Local authenticated deployments can supply PHLO_AUTHZ_TOKEN for read-only verification on the next services start. No development identity was activated."
        )


def wait_observatory_development(
    phlo_dir: Path,
    env: str,
    api_url: str,
    ui_url: str,
    *,
    backend_name: str | None = None,
    timeout: float = 180,
) -> None:
    """Publish a usable URL only after authenticated mandatory capability checks."""
    command = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=get_project_name(),
        backend_name=backend_name,
        profiles=("*",),
    )
    result = run_command(
        [
            *command,
            "exec",
            "-T",
            "phlo-api",
            "python",
            "-m",
            "phlo_api.migrations",
            "--check",
            "--json",
        ],
        check=False,
    )
    try:
        compatible = (
            result.returncode == 0 and json.loads(result.stdout).get("state") == "compatible"
        )
    except (ValueError, TypeError, AttributeError):
        compatible = False
    if not compatible:
        raise click.ClickException(
            "API schema compatibility is not verified. Check the installed phlo_api.migrations module. If migration 004 is required, take a verified backup, stop writers and run --apply --backup-confirmed explicitly. No migration was applied."
        )
    deadline = monotonic() + timeout
    click.echo("Waiting for authenticated Observatory dashboard capabilities...")
    authentication = load_project_model(phlo_dir.parent).authentication
    auth_config = authentication.model_dump() if authentication else {}
    development = (
        auth_config.get("provider") == "static"
        and (auth_config.get("static") or {}).get("dev_mode") is True
    )
    token = get_process_settings().phlo_authz_token
    headers = (
        {"Authorization": f"Bearer {token.get_secret_value()}"} if not development and token else {}
    )
    with httpx.Client(timeout=10, trust_env=False, headers=headers) as client:
        while monotonic() < deadline:
            try:
                checks = _observe_local_dashboard(
                    client, env, api_url, ui_url, development=development
                )
                if checks is not None:
                    report = {
                        "schema_version": 1,
                        "state": "ready",
                        "observed_at": datetime.now(UTC).isoformat(),
                        "verification_method": "development" if development else "bearer",
                        "browser_login_verified": False,
                        "env": env,
                        "url": f"{ui_url}/?env={env}",
                        "capabilities": checks,
                    }
                    write_sensitive_file(phlo_dir / "observatory-ready.json", json.dumps(report))
                    click.echo(f"Open Observatory: {report['url']}")
                    click.echo(
                        "Authenticated dashboard access is verified. Configure additional roles with phlo authz before enabling mutations."
                    )
                    click.echo(
                        "Query preview is optional. See the local Observatory guide for its separate secure opt-in setup."
                    )
                    return
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
            sleep(2)
    raise click.ClickException(
        "Observatory is not ready. Run services logs and services preflight --observatory-dev. Check actual target mapping and /api/v1/readiness. An installed legacy schema requires the explicit backed-up phlo_api.migrations command; startup never applies migration 004. Services and data have been preserved."
    )


def _observe_local_dashboard(
    client: httpx.Client, env: str, api_url: str, ui_url: str, *, development: bool
) -> dict | None:
    """Read the real auth, discovery, dependency and overview contracts independently."""
    responses = [
        client.get(f"{api_url}{path}", params=params)
        for path, params in (
            ("/api/v1/me", {}),
            ("/api/v1/environments", {}),
            ("/api/v1/readiness", {"env": env}),
            ("/api/v1/overview", {"env": env}),
        )
    ]
    if any(response.status_code in {401, 403} for response in responses):
        raise click.ClickException(
            "Local authentication or authorization failed. Review the configured identity and environment-scoped policy; no fallback identity is used."
        )
    if any(response.status_code != 200 for response in responses):
        return None
    me, discovery, readiness, overview = (response.json() for response in responses)
    if (
        not me.get("subject")
        or (development and me["subject"] != "dev_user")
        or discovery.get("default_environment") != env
    ):
        raise click.ClickException(
            "The API identity or configured default differs from this local project. Review PHLO_API_URL and project configuration."
        )
    capabilities = readiness.get("capabilities", {})
    if (
        not all(
            capabilities.get(name)
            and all(check.get("state") == "ready" for check in capabilities[name])
            for name in ("orchestration", "incidents")
        )
        or readiness.get("env") != env
        or overview.get("env") != env
    ):
        return None
    page = client.get(f"{ui_url}/", params={"env": env, "range": "24h"})
    if (
        page.status_code != 200
        or "text/html" not in page.headers.get("content-type", "")
        or "Overview" not in page.text
    ):
        return None
    return capabilities
