"""Init command for initializing Phlo infrastructure.

Creates the .phlo project directory, selects services (expanding
declared dependencies and setup companions), writes phlo.yaml and env
files, and generates docker-compose via ComposeGenerator. Mutation is
authorization-gated, re-init into a non-empty directory is refused, and
production mode rejects default credentials.
"""

import os
import sys
from pathlib import Path

import click
import yaml

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.planner import build_service_selection_plan
from phlo.cli.commands.services.utils import (
    PHLO_CONFIG_FILE,
    PHLO_CONFIG_TEMPLATE,
    _get_env_overrides,
    _warn_secret_env_overrides,
    detect_phlo_source_path,
    expand_service_dependencies,
    get_phlo_dir,
    resolve_phlo_package_dir,
)
from phlo.cli.infrastructure.secure_files import write_sensitive_file
from phlo.cli.infrastructure.utils import parse_env_file
from phlo.cli.output import user_error
from phlo.plugins.compose import ComposeGenerator
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

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
) -> list[ServiceDefinition]:
    """Expand selected services with declared dependencies and setup companions."""
    return expand_service_dependencies(discovery, services)


def _is_uninitialized_phlo_dir(phlo_dir: Path) -> bool:
    """Return true when `.phlo` only contains runtime artifacts created before init."""
    allowed_files = {".DS_Store"}
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


def _get_service_overrides(project_config: dict) -> dict[str, dict]:
    """Read service overrides from the canonical infrastructure block.

    ``services`` at the top level predates ``infrastructure.services`` and is
    retained for compatibility.  The infrastructure form is authoritative when
    both configure the same service, which lets operators keep all container
    configuration in one block.
    """
    legacy = project_config.get("services", {})
    infrastructure = project_config.get("infrastructure", {})
    canonical = infrastructure.get("services", {}) if isinstance(infrastructure, dict) else {}
    if not isinstance(legacy, dict) or not isinstance(canonical, dict):
        raise user_error(
            "invalid phlo.yaml",
            details={"services": "must be a mapping"},
        )

    overrides: dict[str, dict] = {}
    for service_name in set(legacy) | set(canonical):
        if not isinstance(service_name, str) or not service_name:
            raise user_error(
                "invalid phlo.yaml",
                details={"services": "service names must be non-empty strings"},
            )
        legacy_value = legacy.get(service_name, {})
        canonical_value = canonical.get(service_name, {})
        if not isinstance(legacy_value, dict) or not isinstance(canonical_value, dict):
            raise user_error(
                "invalid phlo.yaml",
                details={"services": f"{service_name} must be a mapping"},
            )
        overrides[service_name] = {**legacy_value, **canonical_value}
    return overrides


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
            "then replace any existing default passwords in .phlo/.env.local."
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
    "--production",
    is_flag=True,
    help="Render the production deployment profile without core host ports.",
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
    production: bool,
    profiles: tuple[str, ...],
):
    """Initialize Phlo infrastructure in .phlo/ directory.

    Creates complete Docker Compose configuration for the full Phlo stack:
    - PostgreSQL, MinIO, Nessie (storage layer)
    - Trino (query engine)
    - Dagster (orchestration)
    - Observatory (data platform UI)
    - Superset (BI)
    - pgweb (database admin)
    - Optional: Prometheus, Loki, Grafana (--profile observability)
    - Optional: PostgREST, Hasura (--profile api)

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
    """
    phlo_dir = get_phlo_dir()
    config_file = Path.cwd() / PHLO_CONFIG_FILE

    if phlo_dir.exists() and not force and not _is_uninitialized_phlo_dir(phlo_dir):
        click.echo(f"Directory {phlo_dir} already exists.", err=True)
        click.echo("Use --force to overwrite.", err=True)
        sys.exit(1)

    # Handle conflicting flags
    if dev and no_dev:
        click.echo("Error: Cannot specify both --dev and --no-dev.", err=True)
        sys.exit(1)
    if service_dev and no_dev:
        click.echo("Error: Cannot specify both --service-dev and --no-dev.", err=True)
        sys.exit(1)
    if production and (dev or service_dev):
        click.echo("Error: Production cannot be combined with --dev or --service-dev.", err=True)
        sys.exit(1)

    # --no-dev takes precedence
    if no_dev:
        dev = False
    if production:
        no_dev = True

    # Auto-enable dev mode if we can detect a local Phlo checkout and the user didn't opt out.
    phlo_src_path: str | None = None
    if not dev and not no_dev and not phlo_source and (detected := detect_phlo_source_path()):
        dev = True
        phlo_src_path = detected
        click.echo(f"Dev mode: auto-enabled (path: {phlo_src_path})")

    if service_dev and not dev:
        dev = True
        if not phlo_src_path:
            phlo_src_path = detect_phlo_source_path()

    # Derive project name from directory if not specified
    if not project_name:
        project_name = Path.cwd().name.lower().replace(" ", "-").replace("_", "-")

    # Auto-detect phlo source path for dev mode using flexible detection
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
            # Use flexible path detection
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

    # Create phlo.yaml config file in project root (only if it doesn't exist)
    if not config_file.exists():
        config_content = PHLO_CONFIG_TEMPLATE.format(
            name=project_name,
            description=f"{project_name} data lakehouse",
        )
        config_file.write_text(config_content)
        click.echo(f"Created: {PHLO_CONFIG_FILE}")
    else:
        click.echo(f"Using existing: {PHLO_CONFIG_FILE}")

    # Create .phlo directory
    phlo_dir.mkdir(parents=True, exist_ok=True)

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
    existing_config = {}
    if config_file.exists():
        existing_config = _load_existing_project_config(config_file)
    user_overrides = _get_service_overrides(existing_config)
    # Service selection and inline-service discovery use the same effective
    # overrides as Compose generation.
    existing_config["services"] = user_overrides
    env_overrides = _get_env_overrides(existing_config)
    existing_env_local = parse_env_file(phlo_dir / ".env.local")
    if production:
        _validate_production_credentials(env_overrides, existing_env_local)
        env_overrides = {**env_overrides, "PHLO_ENVIRONMENT": "production"}

    # Collect inline custom services (those with type: inline)
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

    selection_plan = build_service_selection_plan(
        services=all_services,
        config=existing_config,
        profiles=requested_profiles,
        requested_names=[],
    )
    deduped_services: dict[str, ServiceDefinition] = {}
    for service in [*selection_plan.selected_services, *inline_services]:
        deduped_services[service.name] = service
    services_to_install = _expand_selected_services(discovery, list(deduped_services.values()))
    _warn_secret_env_overrides(env_overrides, services_to_install)

    # Generate docker-compose.yml
    composer = ComposeGenerator(discovery)
    compose_content = composer.generate_compose(
        services_to_install,
        phlo_dir,
        dev_mode=dev,
        service_dev_mode=service_dev,
        phlo_src_path=phlo_src_path,
        user_overrides=user_overrides,
        env_values={**os.environ, **env_overrides, **existing_env_local},
        deployment_profile="production" if production else "development",
    )

    compose_file = phlo_dir / "docker-compose.yml"
    compose_file.write_text(compose_content)
    click.echo(f"Created: {compose_file.relative_to(Path.cwd())}")

    # Generate .env + .env.local
    env_file = phlo_dir / ".env"
    env_local_file = phlo_dir / ".env.local"
    env_content = composer.generate_env(services_to_install, env_overrides=env_overrides)
    env_local_content = composer.generate_env_local(
        services_to_install,
        env_overrides=env_overrides,
        existing_values=existing_env_local,
    )
    env_file.write_text(env_content)
    click.echo(f"Created: {env_file.relative_to(Path.cwd())}")
    write_sensitive_file(env_local_file, env_local_content)
    click.echo(f"Created: {env_local_file.relative_to(Path.cwd())}")

    # Generate .gitignore
    gitignore_file = phlo_dir / ".gitignore"
    gitignore_file.write_text(composer.generate_gitignore(services_to_install))
    click.echo(f"Created: {gitignore_file.relative_to(Path.cwd())}")

    # Create volumes directory
    volumes_dir = phlo_dir / "volumes"
    volumes_dir.mkdir(exist_ok=True)

    # Copy service files (Dockerfiles, configs, etc.)
    copied_files = composer.copy_service_files(services_to_install, phlo_dir)
    for f in copied_files:
        click.echo(f"Created: .phlo/{f}")

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
    click.echo("Next steps:")
    click.echo("  1. Commit non-secret defaults in phlo.yaml (env:)")
    click.echo("  2. Set secrets in .phlo/.env.local")
    click.echo("  3. Run: phlo services start")
    click.echo("  4. Inspect services with: phlo services list")
