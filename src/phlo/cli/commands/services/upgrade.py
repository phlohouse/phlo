"""Review generated files and explicit empty-bind ownership, never migrate data."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import click
import yaml
from pydantic import ValidationError

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.utils import (
    _get_env_overrides,
    _get_service_overrides,
    ensure_compose_project,
    expand_service_dependencies,
)
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.infrastructure.utils import parse_env_file
from phlo.config.layout import SHARED_LAYOUT_MARKER, project_env_paths
from phlo.infrastructure.container_backend import (
    project_compose_paths,
    select_project_container_backend,
)
from phlo.operations.api_runtime import (
    pending_directories,
    plan_api_runtime,
    repair_api_runtime,
    verify_api_runtime,
)
from phlo.operations.generated_stack import (
    BASELINE,
    GeneratedStackPlan,
    checked_path,
    file_digest,
    generation_baseline,
    installed_distributions,
    plan_generated_stack,
)
from phlo.plugins.compose.artifacts import split_host_compose
from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.compose.service_files import prepare_service_files
from phlo.plugins.discovery.services import ServiceDefinition, ServiceDiscovery


def _development_activation(
    root: Path, config: dict, production: bool
) -> dict[str, tuple[bytes, int]]:
    """Render explicit local onboarding without writing or adopting existing auth."""
    from phlo.config.env import load_project_env
    from phlo.security.mode import is_regulated

    environment = load_project_env(root).get("PHLO_ENVIRONMENT", "development").lower()
    if production or is_regulated() or environment not in {"dev", "development", "local", "test"}:
        raise ValueError("Development activation is forbidden outside local development")
    from phlo.cli.commands.services.init import _configure_observatory_dev

    baseline_path = checked_path(root, f".phlo/{BASELINE}")
    recorded = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    owned = recorded.get("files", {})
    names = ("phlo.yaml", ".phlo/authorization/roles.yaml", ".phlo/authorization/policies.yaml")
    # Only a previous reviewed activation can be repeated. Existing OIDC or
    # hand-written roles are never adopted or converted to development auth.
    if config.get("authentication"):
        if config["authentication"] != {
            "provider": "static",
            "static": {"enabled": True, "dev_mode": True},
        } or any(name not in owned or file_digest(root, name) != owned[name] for name in names):
            raise ValueError("Existing or edited authentication requires manual review")
        return {name: (checked_path(root, name).read_bytes(), 0o600) for name in names}
    return {
        path.relative_to(root).as_posix(): (content.encode(), 0o600)
        for path, content in _configure_observatory_dev(
            config, config.get("name") or root.name, root / ".phlo", root / "phlo.yaml", write=False
        ).items()
    }


def _candidate(
    root: Path, production: bool, observatory_dev: bool = False
) -> tuple[dict[str, tuple[bytes, int]], tuple, tuple, tuple, dict]:
    state = root / ".phlo"
    config = yaml.safe_load(checked_path(root, "phlo.yaml").read_text(encoding="utf-8")) or {}
    activation = _development_activation(root, config, production) if observatory_dev else {}
    old = yaml.safe_load(checked_path(root, ".phlo/docker-compose.yml").read_text(encoding="utf-8"))
    if not isinstance(old, dict) or not isinstance(old.get("services"), dict):
        raise ValueError("Existing Compose must contain services")
    if "/opt/phlo-dev" in json.dumps(old):
        raise ValueError(
            "Source-mounted development stacks require an explicit non-dev regeneration"
        )
    discovery = ServiceDiscovery()
    known = discovery.discover()
    overrides = _get_service_overrides(config)
    selected = []
    for name in old["services"]:
        if name in known:
            selected.append(known[name])
        elif overrides.get(name, {}).get("type") == "inline":
            selected.append(ServiceDefinition.from_inline(name, overrides[name]))
    services = expand_service_dependencies(discovery, selected)
    env = {**os.environ, **_get_env_overrides(config)}
    for path in project_env_paths(state):
        checked_path(root, path.relative_to(root).as_posix())
        env.update(parse_env_file(path))
    generator = ComposeGenerator(discovery)
    rendered = yaml.safe_load(
        generator.generate_compose(
            services,
            state,
            user_overrides=overrides,
            env_values=env,
            deployment_profile="production" if production else "development",
        )
    )
    migrations = _retain_storage(old, rendered)
    # Preserve unrelated services and top-level networks/volumes verbatim.
    rendered["services"] = {**old["services"], **rendered["services"]}
    for key, value in old.items():
        if key == "services":
            continue
        if isinstance(value, dict):
            rendered[key] = {**rendered.get(key, {}), **value}
        else:
            rendered.setdefault(key, value)
    marker = checked_path(root, ".phlo/.gitignore")
    shared = marker.exists() and SHARED_LAYOUT_MARKER in marker.read_text(encoding="utf-8")
    files: dict[str, tuple[bytes, int]] = dict(activation)
    if shared and not production:
        rendered, host = split_host_compose(rendered)
        if host["services"]:
            files[".phlo/overrides/compose.host.yaml"] = (yaml.safe_dump(host).encode(), 0o600)
    files[".phlo/docker-compose.yml"] = (yaml.safe_dump(rendered, sort_keys=False).encode(), 0o600)
    prepared = prepare_service_files(services, state, user_overrides=overrides)
    for name, (source, content) in prepared.files.items():
        target = checked_path(root, f".phlo/{name}")
        mode = source.stat().st_mode & 0o777
        if target.exists():
            mode &= target.stat().st_mode & 0o777
        files[f".phlo/{name}"] = (
            content if content is not None else source.read_bytes(),
            mode,
        )
    companions = tuple(name for name in rendered["services"] if name not in old["services"])
    order = tuple(service.name for service in discovery.resolve_dependencies(services))
    images = {
        name: (old["services"].get(name, {}).get("image"), config.get("image"))
        for name, config in rendered["services"].items()
    }
    return files, companions, order, migrations, images


def _retain_storage(old: dict, candidate: dict) -> tuple[str, ...]:
    migrations = []
    for name in ("minio", "minio-setup", "postgres", "nessie"):
        current = old["services"].get(name)
        desired = candidate["services"].get(name)
        if not current or not desired:
            continue
        if current.get("image") != desired.get("image"):
            instruction = (
                "phlo minio migrate-image (preview), then --apply after verified object backup; "
                "validate existing object reads before replanning"
                if name.startswith("minio")
                else f"{name}: provider-owned backup, supported migration and data validation required before replanning"
            )
            if instruction not in migrations:
                migrations.append(instruction)
        if name.endswith("-setup"):
            continue
        # A general template upgrade must never relocate retained data or undo
        # the MinIO image migration's preserved layout/ownership decisions.
        for key in ("volumes", "command", "entrypoint", "user"):
            if key in current:
                desired[key] = current[key]
            else:
                desired.pop(key, None)
    return tuple(migrations)


def build_plan(
    root: Path,
    previous_generated: Path | None,
    production: bool,
    observatory_dev: bool = False,
    repair_empty_api_binds: bool = False,
) -> tuple[GeneratedStackPlan, dict[str, tuple[bytes, int]]]:
    files, companions, order, migrations, images = _candidate(root, production, observatory_dev)
    runtime_ownership = ()
    if repair_empty_api_binds:
        if select_project_container_backend().name != "docker":
            raise ValueError("Empty API bind ownership repair requires the Docker backend")
        document = _validate_compose(root, files, allow_insecure=False)
        runtime_ownership = plan_api_runtime(root, document)
    else:
        service = yaml.safe_load(files[".phlo/docker-compose.yml"][0])["services"].get(
            "phlo-api", {}
        )
        host = files.get(".phlo/overrides/compose.host.yaml")
        if host:
            service.update(yaml.safe_load(host[0])["services"].get("phlo-api", {}))
        if pending_directories(root, service):
            migrations += (
                "API writable binds require ownership review: stop API and replan with --repair-empty-api-binds; non-empty or custom directories require operator reconciliation",
            )
    baseline_path = checked_path(root, f".phlo/{BASELINE}")
    recorded = (
        json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    )
    baseline = recorded.get("files", {})
    if previous_generated:
        if previous_generated.resolve() == root.resolve():
            raise ValueError("The previous baseline must not be the project being upgraded")
        baseline = {name: file_digest(previous_generated, name) for name in files}
        previous_baseline = checked_path(previous_generated, f".phlo/{BASELINE}")
        recorded = (
            json.loads(previous_baseline.read_text(encoding="utf-8"))
            if previous_baseline.exists()
            else {}
        )
    if observatory_dev:
        baseline.update(
            {
                name: file_digest(root, name)
                for name in files
                if not name.startswith(".phlo/") or name.startswith(".phlo/authorization/")
            }
        )
    inputs = ["phlo.yaml", f".phlo/{BASELINE}"]
    if observatory_dev:
        inputs.extend((".phlo/authorization/roles.yaml", ".phlo/authorization/policies.yaml"))
    inputs.extend(path.relative_to(root).as_posix() for path in project_env_paths(root / ".phlo"))
    inputs.append(".phlo/.gitignore")
    inputs.extend(
        path.relative_to(root).as_posix() for path in project_compose_paths(root / ".phlo")
    )
    plan = plan_generated_stack(
        root=root,
        candidate=files,
        baseline=baseline,
        source_distributions=recorded.get("distributions", {}),
        distributions=installed_distributions(),
        images=images,
        inputs=inputs,
        new_companions=companions,
        restart_order=order,
        migrations=migrations,
    )
    plan = plan.model_copy(update={"api_runtime_ownership": runtime_ownership})
    return plan, files


def apply_plan(
    root: Path,
    reviewed: GeneratedStackPlan,
    current: GeneratedStackPlan,
    files: dict[str, tuple[bytes, int]],
    *,
    allow_insecure: bool,
) -> Path:
    backup = root / ".phlo" / "upgrade-backups" / reviewed.token()
    receipt_path = checked_path(root, f".phlo/upgrade-backups/{reviewed.token()}/receipt.json")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("plan_token") == reviewed.token()
            and receipt.get("inputs") == current.inputs
            and reviewed.distributions == current.distributions
            and all(file_digest(root, change.path) == change.after for change in reviewed.files)
            and {change.path: change.after for change in reviewed.files}
            == {change.path: change.after for change in current.files}
        ):
            verify_api_runtime(root, reviewed.api_runtime_ownership, applied=True)
            return backup
        raise ValueError("Applied plan has changed inputs or outputs; review a new plan")
    if reviewed != current:
        raise ValueError("Plan is stale or changed; generate and review a new plan")
    if current.migrations or any(change.state == "conflict" for change in current.files):
        raise ValueError("Resolve conflicts and complete provider migrations before applying")
    if os.name != "posix" and not allow_insecure:
        raise ValueError(
            "Private backup permissions are unsupported; explicit --allow-insecure is required"
        )
    _validate_compose(root, files, allow_insecure=allow_insecure)
    lock = root / ".phlo/upgrade.lock"
    checked_path(root, ".phlo/upgrade.lock/owner.json")
    lock.mkdir(mode=0o700)
    try:
        # Another apply may have completed after this invocation built its plan.
        for name, expected in current.inputs.items():
            if file_digest(root, name) != expected:
                raise ValueError("Plan is stale; inputs changed before apply")
        for change in current.files:
            if file_digest(root, change.path) != change.before:
                raise ValueError("Plan is stale; generated files changed before apply")
            files[change.path][0].decode("utf-8")
        verify_api_runtime(root, current.api_runtime_ownership, applied=False)
        _write_upgrade(root, current, files, backup, allow_insecure=allow_insecure)
    except Exception as exc:
        raise ValueError(
            f"Upgrade failed ({type(exc).__name__}); inspect {backup} and .phlo/upgrade.lock before recovery"
        ) from exc
    lock.rmdir()
    return backup


def _validate_compose(
    root: Path, files: dict[str, tuple[bytes, int]], *, allow_insecure: bool
) -> dict:
    """Resolve candidate layers with the real backend, without applying them."""
    state = root / ".phlo"
    backend = select_project_container_backend()
    command = backend.compose_base_cmd(phlo_dir=state, project_name="phlo-upgrade-review")
    # Rebuild the layer list so a newly generated host layer is validated too,
    # still preceding personal overrides in the canonical precedence order.
    without_layers = []
    arguments = iter(command)
    for argument in arguments:
        if argument == "-f":
            next(arguments)
        else:
            without_layers.append(argument)
    command = without_layers
    with tempfile.TemporaryDirectory(prefix="upgrade-review-", dir=state) as directory:
        for original in project_compose_paths(state):
            relative = original.relative_to(root).as_posix()
            if relative not in files and not original.is_file():
                continue
            replacement = original
            if relative in files:
                replacement = Path(directory) / original.name
                write_sensitive_file(
                    replacement, files[relative][0].decode("utf-8"), allow_insecure=allow_insecure
                )
            command.extend(["-f", str(replacement)])
        result = subprocess.run(
            [
                *command,
                "--profile",
                "*",
                "--project-directory",
                str(state),
                "config",
                "--format",
                "json",
            ],
            cwd=root,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            # Compose errors can include expanded credentials and private paths.
            raise ValueError("Candidate Compose validation failed; inspect configuration privately")
        document = json.loads(result.stdout)
        if "phlo.yaml" in files:
            for name in ("phlo-api", "observatory"):
                service = document["services"][name]
                if service.get("network_mode") == "host" or any(
                    port.get("host_ip") not in {"127.0.0.1", "::1"}
                    for port in service.get("ports", [])
                ):
                    raise ValueError("Development activation requires loopback API/UI bindings")
        return document


def _write_upgrade(
    root: Path,
    current: GeneratedStackPlan,
    files: dict[str, tuple[bytes, int]],
    backup: Path,
    *,
    allow_insecure: bool,
) -> None:
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    changes = [change for change in current.files if change.state != "unchanged"]
    # All backups precede the first replacement. A crash leaves the complete
    # reviewed plan and originals for explicit recovery, never automatic replay.
    write_sensitive_file(
        backup / "plan.json", current.model_dump_json(indent=2), allow_insecure=allow_insecure
    )
    originals: dict[str, tuple[bytes, int] | None] = {}
    for name in [*(change.path for change in changes), f".phlo/{BASELINE}"]:
        target = checked_path(root, name)
        originals[name] = (
            (target.read_bytes(), target.stat().st_mode & 0o777) if target.exists() else None
        )
        if target.exists():
            write_sensitive_file(
                backup / name,
                target.read_text(encoding="utf-8"),
                allow_insecure=allow_insecure,
            )
    write_sensitive_file(
        backup / "original-modes.json",
        json.dumps(
            {name: original[1] if original else None for name, original in originals.items()}
        ),
        allow_insecure=allow_insecure,
    )
    ownership_applied = False
    try:
        repair_api_runtime(root, current.api_runtime_ownership)
        ownership_applied = True
        for change in changes:
            target = checked_path(root, change.path)
            content, mode = files[change.path]
            write_sensitive_file(target, content.decode("utf-8"), allow_insecure=allow_insecure)
            target.chmod(mode)
        baseline = generation_baseline(root, list(files))
        write_sensitive_file(root / ".phlo" / BASELINE, baseline, allow_insecure=allow_insecure)
        write_sensitive_file(
            backup / "receipt.json",
            json.dumps(
                {
                    "plan_token": current.token(),
                    "state": "applied",
                    "inputs": {name: file_digest(root, name) for name in current.inputs},
                }
            ),
            allow_insecure=allow_insecure,
        )
    except Exception:
        for name, original in originals.items():
            target = checked_path(root, name)
            if original is None:
                target.unlink(missing_ok=True)
            else:
                write_sensitive_file(
                    target, original[0].decode("utf-8"), allow_insecure=allow_insecure
                )
                target.chmod(original[1])
        if ownership_applied:
            repair_api_runtime(root, current.api_runtime_ownership, rollback=True)
        raise


@click.command("upgrade")
@click.option("--dry-run", is_flag=True, help="Compare only; this is also the default.")
@click.option(
    "--output", type=click.Path(path_type=Path), help="Save a private reviewable JSON plan."
)
@click.option(
    "--apply",
    "plan_path",
    type=click.Path(exists=True, path_type=Path),
    help="Apply this reviewed plan, never regenerate blindly.",
)
@click.option("--confirm", help="Exact plan token printed by the dry run.")
@click.option(
    "--previous-generated",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Pristine previous-release project for stacks without a generation baseline.",
)
@click.option("--production", is_flag=True, help="Keep the production rendering profile.")
@click.option(
    "--observatory-dev",
    is_flag=True,
    help="Include explicit local-development activation in the reviewed plan; requires actual configured environments.",
)
@click.option(
    "--repair-empty-api-binds",
    is_flag=True,
    help="Review ownership-only repair of empty root-owned default API binds on Linux; requires a stopped API and local image.",
)
@click.option(
    "--allow-insecure",
    is_flag=True,
    help="Explicitly accept platforms without POSIX private-file guarantees.",
)
@require_mutation_authorization(
    "services.upgrade", when=lambda kwargs: kwargs.get("plan_path") is not None
)
def upgrade_cmd(
    dry_run: bool,
    output: Path | None,
    plan_path: Path | None,
    confirm: str | None,
    previous_generated: Path | None,
    production: bool,
    observatory_dev: bool,
    repair_empty_api_binds: bool,
    allow_insecure: bool,
) -> None:
    """Reconcile installed templates, preserving secrets, volumes and personal overrides.

    No services are restarted and no data is migrated. Empty API bind ownership
    changes require explicit opt-in and review. Pause writers and complete
    provider data migrations separately; container health is not data validation.
    """
    root = ensure_compose_project().parent
    try:
        if dry_run and plan_path:
            raise ValueError("--dry-run and --apply are mutually exclusive")
        current, files = build_plan(
            root, previous_generated, production, observatory_dev, repair_empty_api_binds
        )
        if plan_path:
            reviewed = GeneratedStackPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
            if confirm != reviewed.token():
                raise ValueError("--confirm must match the reviewed plan token")
            backup = apply_plan(root, reviewed, current, files, allow_insecure=allow_insecure)
            click.echo(
                f"Applied {'files and reviewed ownership repairs' if reviewed.api_runtime_ownership else 'files only'}. Private originals and recovery plan: {backup}"
            )
        else:
            if output:
                write_sensitive_file(
                    output, current.model_dump_json(indent=2), allow_insecure=allow_insecure
                )
            # Full candidate content belongs only in the private review file;
            # project replacements can contain literals that must not hit logs.
            click.echo(current.model_dump_json(indent=2, exclude={"candidate_files"}))
            click.echo(f"Plan token: {current.token()}")
    except ValidationError as exc:
        raise click.ClickException("Invalid upgrade plan; inspect the private plan file") from exc
    except yaml.YAMLError as exc:
        raise click.ClickException("Invalid YAML input; no upgrade files were applied") from exc
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
