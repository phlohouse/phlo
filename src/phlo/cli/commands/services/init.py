"""Init command for initializing Phlo infrastructure.

Creates the .phlo project directory, selects services (expanding
declared dependencies and setup companions), writes phlo.yaml and env
files, and generates docker-compose via ComposeGenerator. Mutation is
authorization-gated, re-init into a non-empty directory is refused, and
production mode rejects default credentials.
"""

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

import click
import yaml

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.planner import ServiceSelectionPlan, build_service_selection_plan
from phlo.cli.commands.services.utils import (
    PHLO_CONFIG_FILE,
    PHLO_CONFIG_TEMPLATE,
    UV_LOCK_METADATA_FILES,
    _get_env_overrides,
    _get_service_overrides,
    _warn_secret_env_overrides,
    apply_action_defaults,
    apply_notebook_defaults,
    apply_preview_defaults,
    apply_uv_lock_env_override,
    detect_phlo_source_path,
    expand_service_dependencies,
    generate_compose_or_fail,
    get_phlo_dir,
    grant_dev_access,
    prepare_api_runtime_directories,
    resolve_phlo_package_dir,
)
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.infrastructure.utils import parse_env_file
from phlo.cli.output import user_error
from phlo.config.layout import SHARED_LAYOUT_MARKER, env_defaults_path, env_secrets_path
from phlo.config_schema import ProjectConfig
from phlo.infrastructure.container_backend import project_container_host_identity
from phlo.operations.generated_stack import BASELINE, generation_baseline
from phlo.plugins.compose.artifacts import render_shared_gitignore, write_compose_layers
from phlo.plugins.compose.generator import ComposeGenerator
from phlo.plugins.compose.service_files import prepare_service_files
from phlo.plugins.discovery.services import ServiceDefinition, ServiceDiscovery

_PRODUCTION_USERNAME_DEFAULTS = {
    "POSTGRES_USER": "phlo",
    "MINIO_ROOT_USER": "minio",
}
_PRODUCTION_PASSWORD_DEFAULTS = {
    "POSTGRES_PASSWORD": "phlo",
    "MINIO_ROOT_PASSWORD": "minio123",
}


def _expand_selected_services(
    discovery: ServiceDiscovery,
    services: list[ServiceDefinition],
    *,
    additional_services: list[ServiceDefinition] | None = None,
    excluded: Iterable[str] = (),
) -> list[ServiceDefinition]:
    """Expand selected services with declared dependencies and setup companions."""
    if not additional_services:
        return expand_service_dependencies(discovery, services, excluded=excluded)
    return expand_service_dependencies(
        discovery,
        services,
        additional_services=additional_services,
        excluded=excluded,
    )


def _is_uninitialized_phlo_dir(phlo_dir: Path) -> bool:
    """Return true when `.phlo` only contains runtime artifacts created before init."""
    allowed_files = {".DS_Store", *UV_LOCK_METADATA_FILES}
    for path in phlo_dir.rglob("*"):
        if path.is_dir():
            if path.relative_to(phlo_dir).parts[:1] == ("logs",):
                continue
            return False
        relative = path.relative_to(phlo_dir)
        if relative.parts[:1] == ("logs",):
            continue
        if path.name in allowed_files:
            continue
        return False
    return True


def _load_existing_project_config(config_file: Path) -> dict:
    try:
        with config_file.open() as f:
            project_config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise user_error(
            "invalid phlo.yaml",
            details={"File": config_file, "Error": exc},
        ) from exc

    if not isinstance(project_config, dict):
        raise user_error(
            "invalid phlo.yaml",
            details={"File": config_file, "Error": "top-level value must be a mapping"},
        )
    return project_config


def _validate_production_credentials(
    env_overrides: dict,
    existing_local_values: dict[str, str],
) -> None:
    """Reject bundled credential values before rendering a production profile."""
    invalid: list[str] = []

    for variable, default in _PRODUCTION_USERNAME_DEFAULTS.items():
        value = env_overrides.get(variable)
        if not isinstance(value, str) or not value.strip() or value.strip() == default:
            invalid.append(variable)

    supplied_passwords: dict[str, str] = {}
    for variable, default in _PRODUCTION_PASSWORD_DEFAULTS.items():
        value = existing_local_values.get(variable, env_overrides.get(variable))
        if value is None:
            continue  # generate_env_local creates an independent secret for new deployments.
        if not isinstance(value, str) or not value.strip() or value.strip() == default:
            invalid.append(variable)
        else:
            supplied_passwords[variable] = value

    if len(set(supplied_passwords.values())) != len(supplied_passwords):
        invalid.extend(sorted(supplied_passwords))

    if invalid:
        variables = ", ".join(sorted(set(invalid)))
        raise click.ClickException(
            "Production credentials must be non-empty, non-default, and independent: "
            f"{variables}. Set POSTGRES_USER and MINIO_ROOT_USER under phlo.yaml env:, "
            "then replace any existing default passwords in .phlo/secrets/.env."
        )


@click.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing configuration")
@click.option("--name", "project_name", help="Project name (default: directory name)")
@click.option(
    "--dev",
    is_flag=True,
    help="Development mode: mount local phlo source for instant iteration",
)
@click.option(
    "--no-dev",
    is_flag=True,
    help="Explicitly disable dev mode (useful when regenerating without dev mounts)",
)
@click.option(
    "--phlo-source",
    type=click.Path(exists=True),
    help="Path to phlo repo root or `src/phlo` (default: auto-detect or PHLO_DEV_SOURCE env var)",
)
@click.option(
    "--service-dev",
    is_flag=True,
    help="Also apply service-specific `dev:` runtime overrides (opt-in)",
)
@click.option(
    "--observatory-dev/--no-observatory-dev",
    default=None,
    help=(
        "Set up the loopback-only Observatory sign-in for the local dev identity. "
        "Development stacks with the API and UI get it by default; "
        "--no-observatory-dev leaves authentication to you."
    ),
)
@click.option(
    "--production",
    is_flag=True,
    help="Render the production deployment profile without core host ports.",
)
@click.option(
    "--publish-all-interfaces",
    is_flag=True,
    help="Publish generated host ports on all interfaces instead of loopback (LAN exposure).",
)
@click.option(
    "--allow-insecure",
    is_flag=True,
    help="Allow generated local secrets on platforms that cannot enforce 0600 permissions.",
)
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    help="Enable optional profile services (e.g., --profile observability --profile api)",
)
@require_mutation_authorization("services.init")
def init_cmd(
    force: bool,
    project_name: str | None,
    dev: bool,
    no_dev: bool,
    phlo_source: str | None,
    service_dev: bool,
    observatory_dev: bool | None,
    production: bool,
    publish_all_interfaces: bool,
    allow_insecure: bool,
    profiles: tuple[str, ...],
):
    """Initialize Phlo infrastructure in .phlo/ directory.

    Renders Docker Compose configuration for the services of the installed
    Phlo packages. With phlo[defaults] that is PostgreSQL, MinIO and Nessie
    (storage), Trino (query), Dagster (orchestration) and, in development,
    phlo-api with Observatory signed in as the local dev user on loopback.
    Other packages add their services when installed; `phlo services add`
    renders optional ones, and --profile renders a whole profile.

    Use --dev to mount local phlo source for development iteration.
    Use --service-dev to opt into service-specific `dev:` runtimes as well.
    Use --no-dev to explicitly generate config without dev mounts.

    Examples:
        phlo services init
        phlo services init --name my-lakehouse
        phlo services init --force
        phlo services init --profile observability
        phlo services init --profile api --profile observability
        phlo services init --dev
        phlo services init --dev --phlo-source ../../src/phlo
        phlo services init --dev --service-dev
        phlo services init --no-dev --force  # Regenerate without dev mode
        phlo services init --production --no-dev
        phlo services init --publish-all-interfaces  # Expose host ports to the LAN
    """
    phlo_dir = get_phlo_dir()
    config_file = Path.cwd() / PHLO_CONFIG_FILE
    if observatory_dev and (production or publish_all_interfaces):
        raise click.UsageError("--observatory-dev requires development mode and loopback ports.")

    marker = phlo_dir / ".gitignore"
    shared_layout = marker.is_file() and SHARED_LAYOUT_MARKER in marker.read_text(encoding="utf-8")
    new_layout = not phlo_dir.exists() or _is_uninitialized_phlo_dir(phlo_dir)
    preserve_shared = shared_layout and not force
    preserve_generation = preserve_shared and (phlo_dir / "docker-compose.yml").exists()
    if (
        phlo_dir.exists()
        and not force
        and not shared_layout
        and not _is_uninitialized_phlo_dir(phlo_dir)
    ):
        click.echo(f"Directory {phlo_dir} already exists.", err=True)
        click.echo("Use --force to overwrite.", err=True)
        sys.exit(1)

    dev, phlo_src_path = _resolve_dev_mode(
        dev,
        no_dev or (bool(observatory_dev) and not dev),
        service_dev,
        production,
        phlo_source,
        phlo_dir,
    )
    # Derive project name from directory if not specified
    if not project_name:
        project_name = Path.cwd().name.lower().replace(" ", "-").replace("_", "-")

    # Create phlo.yaml config file in project root (only if it doesn't exist)
    if not config_file.exists():
        config_content = PHLO_CONFIG_TEMPLATE.format(
            name=project_name,
            description=f"{project_name} data lakehouse",
        )
        config_file.write_text(config_content, encoding="utf-8")
        click.echo(f"Created: {PHLO_CONFIG_FILE}")
    else:
        click.echo(f"Using existing: {PHLO_CONFIG_FILE}")

    # Create .phlo directory
    phlo_dir.mkdir(parents=True, exist_ok=True)
    if new_layout:
        marker.write_text(render_shared_gitignore([]), encoding="utf-8")
        shared_layout = True
    if shared_layout:
        (phlo_dir / "overrides").mkdir(exist_ok=True)
        (phlo_dir / "secrets").mkdir(exist_ok=True)
        attributes = phlo_dir / ".gitattributes"
        if not attributes.exists():
            attributes.write_text("* text=auto eol=lf\n", encoding="utf-8")

    # Discover services
    discovery = ServiceDiscovery()
    all_services = discovery.discover()

    if not all_services:
        click.echo(
            "Error: No services found. Install service plugins or check entry points.",
            err=True,
        )
        sys.exit(1)

    # Load existing phlo.yaml config for user overrides
    existing_config = _load_existing_project_config(config_file)
    signed_in = _apply_observatory_dev(
        existing_config,
        project_name,
        phlo_dir,
        config_file,
        explicit=bool(observatory_dev),
        automatic=observatory_dev is None
        and _wants_local_observatory(
            existing_config, all_services, production, publish_all_interfaces
        ),
    )
    user_overrides = _get_service_overrides(existing_config)
    # Service selection and inline-service discovery use the same effective
    # overrides as Compose generation.
    existing_config["services"] = user_overrides
    env_overrides = _get_env_overrides(existing_config)
    existing_env_local = parse_env_file(env_secrets_path(phlo_dir))
    if production:
        _validate_production_credentials(env_overrides, existing_env_local)
        env_overrides = {**env_overrides, "PHLO_ENVIRONMENT": "production"}

    selection_plan, services_to_install = _select_initial_services(
        discovery, all_services, existing_config, user_overrides, env_overrides, profiles
    )
    env_overrides = apply_notebook_defaults(services_to_install, env_overrides)
    env_overrides = apply_action_defaults(services_to_install, env_overrides)
    env_overrides = apply_preview_defaults(
        services_to_install, env_overrides, existing_config.get("environments")
    )
    _warn_secret_env_overrides(env_overrides, services_to_install)

    # Generate docker-compose.yml
    composer = ComposeGenerator(discovery, host_identity=project_container_host_identity())
    try:
        service_files = prepare_service_files(
            services_to_install, phlo_dir, user_overrides=user_overrides
        )
    except (ValueError, OSError) as exc:
        raise user_error("invalid service file override", details={"Error": str(exc)}) from exc
    # Stage build inputs only after service-file validation succeeds.
    env_overrides = apply_uv_lock_env_override(phlo_dir, env_overrides)
    compose_content = generate_compose_or_fail(
        composer,
        services_to_install,
        phlo_dir,
        dev_mode=dev,
        service_dev_mode=service_dev,
        phlo_src_path=phlo_src_path,
        user_overrides=user_overrides,
        env_values={**os.environ, **env_overrides, **existing_env_local},
        deployment_profile="production" if production else "development",
        publish_all_interfaces=publish_all_interfaces,
    )

    portable_content = None
    if shared_layout and (dev or service_dev):
        portable_content = composer.generate_compose(
            services_to_install,
            phlo_dir,
            user_overrides=user_overrides,
            env_values={**os.environ, **env_overrides, **existing_env_local},
            publish_all_interfaces=publish_all_interfaces,
        )
    compose_file = phlo_dir / "docker-compose.yml"
    write_compose_layers(
        phlo_dir,
        compose_content,
        portable_rendered=portable_content,
        preserve_shared=preserve_shared,
        production=production,
    )
    click.echo(f"Created: {compose_file.relative_to(Path.cwd())}")

    # Generate .env + .env.local
    env_file = env_defaults_path(phlo_dir)
    env_local_file = env_secrets_path(phlo_dir)
    env_content = composer.generate_env(services_to_install, env_overrides=env_overrides)
    env_local_content = composer.generate_env_local(
        services_to_install,
        env_overrides=env_overrides,
        existing_values=existing_env_local,
    )
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(env_content, encoding="utf-8")
    click.echo(f"Created: {env_file.relative_to(Path.cwd())}")
    write_sensitive_file(env_local_file, env_local_content, allow_insecure=allow_insecure)
    click.echo(f"Created: {env_local_file.relative_to(Path.cwd())}")

    _write_initial_gitignore(phlo_dir, shared_layout, composer, services_to_install)

    # Create volumes directory
    volumes_dir = phlo_dir / "volumes"
    volumes_dir.mkdir(exist_ok=True)
    prepare_api_runtime_directories(phlo_dir, (service.name for service in services_to_install))
    grant_dev_access(phlo_dir, services_to_install, existing_config.get("environments") or {})

    # Copy service files (Dockerfiles, configs, etc.)
    copied_files = service_files.write(overwrite=not preserve_shared)
    for f in copied_files:
        click.echo(f"Created: .phlo/{f}")

    # Never adopt existing customized files during a preserving re-init.
    _record_initial_generation(
        phlo_dir,
        copied_files,
        preserve_existing=preserve_generation,
        allow_insecure=allow_insecure,
        sign_in=signed_in,
    )

    # Summary
    click.echo("")
    click.echo("Phlo infrastructure initialized.")
    click.echo("")

    default_services = discovery.get_default_services(
        disabled_services=set(selection_plan.disabled_names)
    )
    default_names = sorted([s.name for s in default_services])
    click.echo(f"Default services: {', '.join(default_names)}")

    available_profiles = discovery.get_available_profiles()
    if available_profiles:
        click.echo(f"Optional profiles: {', '.join(sorted(available_profiles))}")

    click.echo("")
    click.echo(f"Generated credentials are in {env_local_file.relative_to(Path.cwd())}.")
    click.echo("")
    click.echo("Next steps:")
    click.echo("  1. Run: phlo services start")
    click.echo("  2. Check the stack with: phlo doctor")


def _record_initial_generation(
    phlo_dir: Path,
    copied_files: list[str],
    *,
    preserve_existing: bool,
    allow_insecure: bool,
    sign_in: bool = False,
) -> None:
    if preserve_existing:
        return
    generated = [".phlo/docker-compose.yml", *(f".phlo/{name}" for name in copied_files)]
    if sign_in:
        # The local sign-in files are generated too; record them so a later
        # reviewed upgrade can repeat the activation instead of refusing it.
        generated.extend(_SIGN_IN_FILES)
    if (phlo_dir / "overrides/compose.host.yaml").is_file():
        generated.append(".phlo/overrides/compose.host.yaml")
    baseline = json.loads(generation_baseline(Path.cwd(), generated))
    if not sign_in:
        # A re-init leaves an earlier automatic sign-in alone; keep its record.
        baseline["files"].update(_recorded_sign_in(phlo_dir / BASELINE))
        baseline["files"] = dict(sorted(baseline["files"].items()))
    write_sensitive_file(
        phlo_dir / BASELINE,
        json.dumps(baseline, indent=2) + "\n",
        allow_insecure=allow_insecure,
    )


_SIGN_IN_FILES = (
    "phlo.yaml",
    ".phlo/authorization/roles.yaml",
    ".phlo/authorization/policies.yaml",
)


def _recorded_sign_in(path: Path) -> dict[str, str | None]:
    try:
        recorded = json.loads(path.read_text(encoding="utf-8")).get("files") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {name: recorded[name] for name in _SIGN_IN_FILES if name in recorded}


def _apply_observatory_dev(
    config: dict,
    project_name: str,
    phlo_dir: Path,
    config_file: Path,
    *,
    explicit: bool,
    automatic: bool,
) -> bool:
    """Set up the local Observatory sign-in when asked, or by default in development.

    Observatory is part of the default local stack: with the API and UI
    installed and no sign-in configured, the loopback dev identity is set up
    without a flag. Only the explicit flag turns a refusal into an error.
    """
    if explicit:
        _configure_observatory_dev(config, project_name, phlo_dir, config_file)
        return True
    if automatic:
        try:
            _configure_observatory_dev(config, project_name, phlo_dir, config_file)
        except click.ClickException as exc:
            click.echo(f"Local Observatory sign-in not configured: {exc.message}", err=True)
            return False
        return True
    return False


def _render_project_config(config_file: Path, original: dict, config: dict) -> str:
    """Return phlo.yaml with ``config``'s changes, keeping the file's comments.

    New top-level sections are appended to the existing text. When a section
    that already exists changes, the whole file is rewritten from ``config``.
    """
    try:
        text = config_file.read_text(encoding="utf-8")
    except OSError:
        text = ""
    changed = [key for key in config if config[key] != original.get(key)]
    if not text.strip() or any(key in original for key in changed):
        return yaml.safe_dump(config, sort_keys=False)
    added = yaml.safe_dump({key: config[key] for key in changed}, sort_keys=False)
    return f"{text.rstrip()}\n\n# Local Observatory sign-in (development only)\n{added}"


def _wants_local_observatory(
    config: dict,
    all_services: dict,
    production: bool,
    publish_all_interfaces: bool,
) -> bool:
    """Return whether init should set up the local Observatory without a flag.

    Only a development, loopback-only stack with the API and UI installed and
    no authentication of its own qualifies. Anything else keeps its settings.
    """
    from phlo.config.env import load_project_env
    from phlo.security.mode import is_regulated

    if production or publish_all_interfaces or is_regulated():
        return False
    if not {"phlo-api", "observatory"} <= set(all_services):
        return False
    if config.get("authentication"):
        return False
    services = config.get("services") or {}
    if any(
        (services.get(name) or {}).get("enabled") is False for name in ("phlo-api", "observatory")
    ):
        return False
    environment = load_project_env(Path.cwd()).get("PHLO_ENVIRONMENT", "development")
    return environment.lower() in {"dev", "development", "local", "test"}


def _configure_observatory_dev(
    config: dict,
    project_name: str,
    phlo_dir: Path,
    config_file: Path,
    *,
    write: bool = True,
) -> dict[Path, str]:
    """Add the local development identity through the existing project config."""
    import copy

    from phlo.config.env import load_project_env
    from phlo.security.mode import is_regulated

    original = copy.deepcopy(config)

    env = config.setdefault("env", {})
    effective = load_project_env(config_file.parent)
    environment = effective.get("PHLO_ENVIRONMENT", "development")
    if is_regulated() or environment.lower() not in {"dev", "development", "local", "test"}:
        raise click.ClickException(
            "Observatory development identity is forbidden outside development."
        )
    if any(
        effective.get(name) not in {None, "", "static"}
        for name in ("PHLO_AUTHENTICATION_PROVIDER", "PHLO_AUTHENTICATION_METHOD")
    ):
        raise click.ClickException(
            "Remove the conflicting authentication provider before local onboarding."
        )
    if effective.get("PHLO_V1_ENVIRONMENTS"):
        raise click.ClickException(
            "Move runtime environment mappings into phlo.yaml before development onboarding."
        )
    if not config.get("environments") and any(
        config.get("services", {}).get(name) for name in ("dagster", "nessie")
    ):
        raise click.ClickException(
            "Configure the actual Dagster location, Nessie ref and local Compose project in phlo.yaml environments before onboarding a customized provider stack."
        )
    if config.get("authentication"):
        raise click.ClickException(
            "Existing authentication must be reviewed manually; it will not be overwritten."
        )
    if config.get("api") or config.get("services", {}).get("phlo-api", {}).get("authorization"):
        raise click.ClickException("Existing API authorization will not be overwritten.")
    authorization = phlo_dir / "authorization"
    if any((authorization / name).exists() for name in ("roles.yaml", "policies.yaml")):
        raise click.ClickException("Existing authorization files will not be overwritten.")
    config["authentication"] = {"provider": "static", "static": {"enabled": True, "dev_mode": True}}
    config["api"] = {"authorization": {"backend": "default", "mode": "required"}}
    env.update({"PHLO_ENVIRONMENT": "development", "PHLO_AUTHORIZATION_MODE": "required"})
    config.setdefault(
        "environments",
        {
            "sandbox": {
                "dagster_location": "phlo_dagster.framework.definitions",
                "nessie_ref": effective.get("NESSIE_DEFAULT_REF", "main"),
                "compose_project": config.get("name") or project_name,
            },
        },
    )
    config.setdefault("default_environment", next(iter(config["environments"])))
    services = config.setdefault("services", {})
    for name in ("phlo-api", "observatory"):
        services.setdefault(name, {})["enabled"] = True
    services["phlo-api"].setdefault("environment", {}).update(
        {
            "PHLO_ENVIRONMENT": "development",
            "PHLO_AUTH_DEV_MODE": "true",
            "PHLO_AUTHENTICATION_PROVIDER": "static",
            "PHLO_AUTHORIZATION_MODE": "required",
        }
    )
    ProjectConfig.model_validate(config)
    for name in ("phlo-api", "observatory"):
        if services[name].get("ports"):
            raise click.ClickException(
                "Review custom UI/API port overrides before development onboarding."
            )
    policies = [
        {
            "policy_id": "observatory-dev-discovery",
            "effect": "allow",
            "principal": {"roles": ["admin"]},
            "action": "platform_metadata.read",
            "resource": {"type": "platform_metadata", "id_pattern": "*"},
        }
    ] + [
        {
            "policy_id": f"observatory-dev-read-{environment}-{index}",
            "effect": "allow",
            "principal": {"roles": ["admin"]},
            "action": "*.read",
            "resource": {"type": "*", "id_pattern": pattern},
        }
        for environment in config["environments"]
        for index, pattern in enumerate((f"env={environment}", f"env={environment}|*"))
    ]
    files = {
        config_file: _render_project_config(config_file, original, config),
        authorization / "roles.yaml": yaml.safe_dump(
            {"roles": {"admin": {}}, "subjects": {"users": {"dev_user": ["admin"]}}}
        ),
        authorization / "policies.yaml": yaml.safe_dump({"policies": policies}),
    }
    if write:
        authorization.mkdir(parents=True, exist_ok=True)
        for path, content in files.items():
            path.write_text(content, encoding="utf-8")
        from phlo.config.project import clear_config_cache

        clear_config_cache()
        click.echo("Configured the local Observatory sign-in (dev_user, loopback only).")
    return files


def _select_initial_services(
    discovery: ServiceDiscovery,
    all_services: dict[str, ServiceDefinition],
    existing_config: dict,
    user_overrides: dict,
    env_overrides: dict,
    profiles: tuple[str, ...],
) -> tuple[ServiceSelectionPlan, list[ServiceDefinition]]:
    inline_services = [
        ServiceDefinition.from_inline(name, cfg)
        for name, cfg in user_overrides.items()
        if isinstance(cfg, dict) and cfg.get("type") == "inline"
    ]
    requested_profiles = tuple(
        dict.fromkeys(profile.strip() for profile in profiles if profile.strip())
    )
    available_profiles = discovery.get_available_profiles()
    unknown_profiles = sorted(set(requested_profiles) - available_profiles)
    if unknown_profiles:
        click.echo(
            f"Error: Unknown profile(s): {', '.join(unknown_profiles)}. "
            f"Available profiles: {', '.join(sorted(available_profiles)) or '(none)'}",
            err=True,
        )
        sys.exit(1)
    if "observability" in requested_profiles:
        env_overrides.setdefault("OBSERVE_HTTP_ENDPOINT", "http://localhost:10010/v1/events")
    selection_plan = build_service_selection_plan(
        services=all_services,
        config=existing_config,
        profiles=requested_profiles,
        requested_names=[],
    )
    deduped_services: dict[str, ServiceDefinition] = {}
    for service in [*selection_plan.selected_services, *inline_services]:
        deduped_services[service.name] = service
    services_to_install = _expand_selected_services(
        discovery,
        list(deduped_services.values()),
        additional_services=inline_services,
        excluded=selection_plan.disabled_names,
    )
    if any(service.name == "phlo-observer" for service in services_to_install):
        # Also when the observer is enabled in phlo.yaml rather than by profile.
        env_overrides.setdefault("OBSERVE_HTTP_ENDPOINT", "http://localhost:10010/v1/events")
    return selection_plan, services_to_install


def _write_initial_gitignore(
    phlo_dir: Path,
    shared_layout: bool,
    composer: ComposeGenerator,
    services: list[ServiceDefinition],
) -> None:
    gitignore_file = phlo_dir / ".gitignore"
    if shared_layout:
        generated_ignore = composer.generate_gitignore(services)
        existing_ignore = (
            gitignore_file.read_text(encoding="utf-8") if gitignore_file.exists() else ""
        )
        generated_ignore = render_shared_gitignore([], generated_ignore + existing_ignore)
        gitignore_file.write_text(generated_ignore, encoding="utf-8")
    elif not gitignore_file.exists():
        gitignore_file.write_text(".env\n.env.local\nvolumes/\n", encoding="utf-8")
    click.echo(f"Created: {gitignore_file.relative_to(Path.cwd())}")


def _resolve_dev_mode(
    dev: bool,
    no_dev: bool,
    service_dev: bool,
    production: bool,
    phlo_source: str | None,
    phlo_dir: Path,
) -> tuple[bool, str | None]:
    if dev and no_dev:
        click.echo("Error: Cannot specify both --dev and --no-dev.", err=True)
        sys.exit(1)
    if service_dev and no_dev:
        click.echo("Error: Cannot specify both --service-dev and --no-dev.", err=True)
        sys.exit(1)
    if production and (dev or service_dev):
        click.echo("Error: Production cannot be combined with --dev or --service-dev.", err=True)
        sys.exit(1)
    if no_dev:
        dev = False
    if production:
        no_dev = True

    phlo_src_path: str | None = None
    if not dev and not no_dev and not phlo_source and (detected := detect_phlo_source_path()):
        dev = True
        phlo_src_path = detected
        click.echo(f"Dev mode: auto-enabled (path: {phlo_src_path})")
    if service_dev and not dev:
        dev = True
        if not phlo_src_path:
            phlo_src_path = detect_phlo_source_path()
    if dev:
        if phlo_source:
            phlo_source_path = Path(phlo_source)
            if not phlo_source_path.is_absolute():
                phlo_source_path = (Path.cwd() / phlo_source_path).resolve()
            else:
                phlo_source_path = phlo_source_path.resolve()
            resolved_phlo_source = resolve_phlo_package_dir(phlo_source_path)
            if not resolved_phlo_source:
                click.echo(
                    "Error: --phlo-source must point to the phlo repo root or `src/phlo` package.",
                    err=True,
                )
                sys.exit(1)
            phlo_src_path = str(os.path.relpath(resolved_phlo_source, phlo_dir))
            click.echo(f"Dev mode: using phlo source at {resolved_phlo_source}")
        elif not phlo_src_path:
            phlo_src_path = detect_phlo_source_path()
            if phlo_src_path:
                click.echo(f"Dev mode: auto-detected phlo source (path: {phlo_src_path})")
            else:
                click.echo(
                    "Warning: --dev specified but could not auto-detect phlo source.", err=True
                )
                click.echo(
                    "Set PHLO_DEV_SOURCE env var or use --phlo-source to specify the path.",
                    err=True,
                )
                dev = False
    return dev, phlo_src_path
