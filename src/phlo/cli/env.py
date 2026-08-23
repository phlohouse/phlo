"""
Environment Configuration Commands

Commands for exporting generated environment configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml

from phlo.cli.infrastructure.utils import parse_env_file
from phlo.logging import get_logger
from phlo.plugins.compose import ComposeGenerator
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery

logger = get_logger(__name__)


@click.group()
def env() -> None:
    """Manage environment configuration."""


@env.command("export")
@click.option(
    "--include-secrets",
    is_flag=True,
    help="Include secrets from .phlo/.env.local in the export output.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write output to a file instead of stdout.",
)
@click.option(
    "--format",
    "_format",
    type=click.Choice(["dotenv"], case_sensitive=False),
    default="dotenv",
    help="Output format (dotenv only for now).",
)
def export_env(include_secrets: bool, output: Path | None, _format: str) -> None:
    """Export the generated environment configuration.

    Examples:
        phlo env export
        phlo env export --include-secrets
        phlo env export --output env.full

    With ``--include-secrets`` the output embeds real values from
    ``.phlo/.env.local``; do not write it to a committed location.

    """
    config = _load_project_config()
    env_overrides = _get_env_overrides(config)
    logger.info(
        "env_export_started",
        include_secrets=include_secrets,
        output_file=output is not None,
        output_format=_format,
    )

    discovery = ServiceDiscovery()
    all_services = discovery.discover()
    if not all_services:
        logger.warning("env_export_no_services_found")
        raise click.ClickException(
            "No services found. Install service plugins or run from a Phlo project directory."
        )

    services_to_install = _select_services(discovery, all_services, config)
    composer = ComposeGenerator(discovery)

    env_content = composer.generate_env(services_to_install, env_overrides=env_overrides)

    if include_secrets:
        env_local_path = Path.cwd() / ".phlo" / ".env.local"
        existing_env_local = parse_env_file(env_local_path)
        env_local_content = composer.generate_env_local(
            services_to_install,
            env_overrides=env_overrides,
            existing_values=existing_env_local,
        )
        content = f"{env_content.rstrip()}\n\n{env_local_content.lstrip()}"
    else:
        content = env_content

    if output:
        output.write_text(content)
        logger.info("env_export_succeeded", output_file=str(output))
        click.echo(f"Wrote: {output}")
    else:
        logger.info("env_export_succeeded", output_file=None)
        click.echo(content)


def _load_project_config() -> dict[str, Any]:
    config_path = Path.cwd() / "phlo.yaml"
    if not config_path.exists():
        return {}
    try:
        with config_path.open() as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("env_export_config_load_failed", path=str(config_path), exc_info=True)
        raise click.ClickException(f"Failed to read {config_path}.") from None


def _get_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    env_overrides = config.get("env", {}) if isinstance(config, dict) else {}
    return env_overrides if isinstance(env_overrides, dict) else {}


def _select_services(
    discovery: ServiceDiscovery,
    all_services: dict[str, ServiceDefinition],
    config: dict[str, Any],
) -> list[ServiceDefinition]:
    user_overrides = config.get("services", {}) if isinstance(config, dict) else {}

    disabled_services = {
        name
        for name, cfg in user_overrides.items()
        if isinstance(cfg, dict) and cfg.get("enabled") is False
    }

    inline_services = [
        ServiceDefinition.from_inline(name, cfg)
        for name, cfg in user_overrides.items()
        if isinstance(cfg, dict) and cfg.get("type") == "inline"
    ]

    default_services = discovery.get_default_services(disabled_services=disabled_services)
    profile_services = [
        service
        for service in all_services.values()
        if service.profile and service.name not in disabled_services
    ]

    return default_services + profile_services + inline_services
