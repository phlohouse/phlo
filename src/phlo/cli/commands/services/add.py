"""Add command for rendering optional services into the project stack.

Persists the enabled/disabled service state to phlo.yaml, re-renders the
compose stack, and starts newly-added services unless --no-start is given.
The mutation requires authorization before any state changes.
"""

from __future__ import annotations

from pathlib import Path

import click
import yaml

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.common import (
    parse_service_args,
    run_compose,
    validate_requested_profiles,
)
from phlo.cli.commands.services.utils import (
    PHLO_CONFIG_FILE,
    _regenerate_compose,
    get_phlo_dir,
    get_profile_service_names,
    normalize_services_enabled_disabled_config,
)
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import user_error
from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDiscovery

logger = get_logger(__name__)


def _load_project_config(config_file: Path) -> dict:
    """Load project config, ensuring a mapping root."""
    if config_file.exists():
        try:
            with config_file.open() as handle:
                config = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise user_error(
                "invalid phlo.yaml",
                details={"File": config_file, "Error": exc},
            ) from exc
        if not isinstance(config, dict):
            logger.error("services_add_invalid_config_mapping", config_file=str(config_file))
            raise user_error(
                "invalid phlo.yaml",
                details={"File": config_file, "Error": "top-level value must be a mapping"},
            )
        return config

    logger.error("services_add_missing_config", config_file=str(config_file))
    raise click.ClickException("phlo.yaml not found.")


def _update_config_enabled_services(
    config: dict,
    *,
    services_to_enable: list[str],
) -> tuple[list[str], list[str]]:
    """Persist enabled/disabled service state into phlo.yaml."""
    enabled_names, disabled_names = normalize_services_enabled_disabled_config(config)
    enabled_set = set(enabled_names)
    disabled_set = set(disabled_names)

    for service_name in services_to_enable:
        enabled_set.add(service_name)
        disabled_set.discard(service_name)

    services_config = config.setdefault("services", {})
    if not isinstance(services_config, dict):
        services_config = {}
        config["services"] = services_config

    services_config["enabled"] = sorted(enabled_set)
    services_config["disabled"] = sorted(disabled_set)
    return services_config["enabled"], services_config["disabled"]


def _start_services(
    *,
    phlo_dir: Path,
    project_name: str,
    profile_names: tuple[str, ...],
    service_names: list[str],
) -> None:
    """Start newly-added services."""
    cmd = compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name=project_name,
        profiles=profile_names,
    )
    cmd.extend(["up", "-d", *service_names])
    try:
        result = run_compose(cmd, check=False, capture_output=False)
    except click.ClickException:
        logger.error(
            "services_add_start_exception",
            project_name=project_name,
            profile_count=len(profile_names),
            service_count=len(service_names),
            exc_info=True,
        )
        click.echo("Warning: Could not start services.", err=True)
        click.echo(f"Command: {' '.join(cmd)}", err=True)
        return

    if result.returncode != 0:
        logger.warning(
            "services_add_start_failed",
            project_name=project_name,
            profile_count=len(profile_names),
            service_count=len(service_names),
            returncode=result.returncode,
        )
        click.echo("Warning: Could not start requested services.", err=True)
        click.echo(f"Command: {' '.join(cmd)}", err=True)
        return

    logger.info(
        "services_add_start_succeeded",
        project_name=project_name,
        profile_count=len(profile_names),
        service_count=len(service_names),
    )


@click.command("add")
@click.argument("service_name", required=False)
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    help="Render all services from an optional profile (e.g., --profile api)",
)
@click.option(
    "--service",
    "services",
    multiple=True,
    help="Render explicit service(s) (e.g., --service phlo-api --service observatory)",
)
@click.option("--no-start", is_flag=True, help="Don't start newly-added services after rendering")
@require_mutation_authorization("services.add")
def add_cmd(
    service_name: str | None,
    profiles: tuple[str, ...],
    services: tuple[str, ...],
    no_start: bool,
) -> None:
    """Add optional services or profiles to the rendered project stack.

    Examples:
        phlo services add phlo-api
        phlo services add --profile api
        phlo services add observability
        phlo services add --service phlo-api --service observatory --no-start
    """
    phlo_dir = get_phlo_dir()
    config_file = Path.cwd() / PHLO_CONFIG_FILE
    logger.info(
        "services_add_requested",
        positional_service=service_name,
        profile_count=len(profiles),
        explicit_service_arg_count=len(services),
        no_start=no_start,
    )

    if not phlo_dir.exists():
        logger.error("services_add_missing_phlo_dir", phlo_dir=str(phlo_dir))
        raise click.ClickException(".phlo directory not found. Run 'phlo services init' first.")

    config = _load_project_config(config_file)
    discovery = ServiceDiscovery()
    all_services = discovery.discover()

    normalized_profiles = validate_requested_profiles(profiles)
    explicit_services = parse_service_args(services)
    # A bare positional name that matches a profile but no service is treated
    # as a profile request, so `add observability` behaves like `--profile
    # observability`.
    if service_name:
        available_profiles = discovery.get_available_profiles()
        if service_name in available_profiles and service_name not in all_services:
            normalized_profiles = tuple(dict.fromkeys([*normalized_profiles, service_name]))
        else:
            explicit_services = [service_name, *explicit_services]
            explicit_services = list(dict.fromkeys(explicit_services))

    if not normalized_profiles and not explicit_services:
        raise click.ClickException("Specify a service name, --service, or --profile.")

    unknown_services = [name for name in explicit_services if name not in all_services]
    if unknown_services:
        raise click.ClickException(f"Unknown service name(s): {', '.join(unknown_services)}")

    profile_services = get_profile_service_names(normalized_profiles)
    services_to_enable = list(dict.fromkeys([*profile_services, *explicit_services]))

    if not services_to_enable:
        raise click.ClickException("Nothing to add.")

    _update_config_enabled_services(config, services_to_enable=services_to_enable)

    with config_file.open("w") as handle:
        yaml.dump(config, handle, default_flow_style=False, sort_keys=False)

    logger.info(
        "services_add_config_updated",
        service_count=len(services_to_enable),
        profile_count=len(normalized_profiles),
    )
    click.echo(f"Updated: {PHLO_CONFIG_FILE}")

    _regenerate_compose(discovery, config, phlo_dir)

    if normalized_profiles:
        click.echo(f"Added profiles: {', '.join(normalized_profiles)}")
    if explicit_services:
        click.echo(f"Added services: {', '.join(explicit_services)}")

    if no_start:
        return

    click.echo("")
    click.echo("Starting newly-added services...")
    _start_services(
        phlo_dir=phlo_dir,
        project_name=get_project_name(),
        profile_names=normalized_profiles,
        service_names=services_to_enable,
    )
    click.echo("Services added and started.")
