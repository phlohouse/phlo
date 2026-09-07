"""
Configuration Management Commands

Commands for managing phlo.yaml infrastructure configuration.
"""

import sys
from pathlib import Path

import click
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from phlo.cli.contract import PhloCommand, PhloGroup
from phlo.cli.output import json_envelope, user_error
from phlo.config_schema import ApiConfig, InfrastructureConfig, ServiceOverride
from phlo.infrastructure import clear_config_cache, load_infrastructure_config
from phlo.logging import get_logger

console = Console()
error_console = Console(stderr=True)
logger = get_logger(__name__)


def _load_phlo_yaml(config_path: Path) -> dict:
    try:
        with config_path.open() as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("config_yaml_invalid", path=str(config_path), error=str(exc))
        raise user_error("invalid phlo.yaml", details={"File": config_path, "Error": exc}) from exc


@click.group(cls=PhloGroup)
def config():
    """Manage infrastructure configuration."""


@config.command("show", cls=PhloCommand)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option(
    "--format",
    type=click.Choice(["yaml", "json"]),
    default="yaml",
    help="Output format",
)
def show(format: str, output_json: bool = False):
    """Show the effective infrastructure configuration.

    \b
    Examples:
      phlo config show
      phlo config show --format json
    """
    try:
        infra_config = load_infrastructure_config()
    except yaml.YAMLError as exc:
        config_path = Path.cwd() / "phlo.yaml"
        logger.warning("config_show_yaml_invalid", path=str(config_path), error=str(exc))
        raise user_error("invalid phlo.yaml", details={"File": config_path, "Error": exc}) from exc
    logger.info("config_show_succeeded", output_format=format)

    if output_json:
        click.echo(json_envelope(data={"infrastructure": infra_config.model_dump(mode="json")}))
        return

    if format == "yaml":
        config_dict = infra_config.model_dump(exclude_none=False)
        yaml_output = yaml.dump(
            {"infrastructure": config_dict},
            default_flow_style=False,
            sort_keys=False,
        )
        syntax = Syntax(yaml_output, "yaml", theme="monokai", line_numbers=False)
        console.print("\n[bold]Effective Infrastructure Configuration:[/bold]\n")
        console.print(syntax)
    else:
        config_dict = infra_config.model_dump(exclude_none=False)
        console.print_json(data={"infrastructure": config_dict})


@config.command("validate", cls=PhloCommand)
@click.option("--json", "output_json", is_flag=True, help="Emit structured validation results.")
def validate(output_json: bool = False):
    """Validate infrastructure configuration in phlo.yaml.

    \b
    Examples:
      phlo config validate
    """
    config_path = Path.cwd() / "phlo.yaml"

    if not config_path.exists():
        logger.warning("config_validate_file_missing", path=str(config_path))
        raise user_error("No phlo.yaml found in current directory", run="phlo services init")

    if not output_json:
        console.print(f"Validating: {config_path}\n")

    project_config = _load_phlo_yaml(config_path)

    if not project_config:
        logger.warning("config_validate_empty_file", path=str(config_path))
        raise user_error("phlo.yaml is empty", run="phlo config validate")

    try:
        if "infrastructure" in project_config:
            infra_data = project_config["infrastructure"]
            infra_config = InfrastructureConfig(**infra_data)
        else:
            infra_config = InfrastructureConfig()

        api_data = project_config.get("api")
        if api_data is not None:
            ApiConfig(**api_data)

        services_data = project_config.get("services", {})
        if isinstance(services_data, dict):
            for service_name, service_config in services_data.items():
                if not isinstance(service_config, dict):
                    continue
                if service_name in {"enabled", "disabled"}:
                    continue
                ServiceOverride(**service_config)
    except ValidationError as e:
        logger.warning(
            "config_validate_failed",
            path=str(config_path),
            error_count=len(e.errors()),
        )
        raise user_error(
            "Validation Error",
            details=[
                f"{' -> '.join(str(x) for x in error['loc'])}: {error['msg']}"
                for error in e.errors()
            ],
            run="phlo config validate",
        ) from e

    logger.info(
        "config_validate_succeeded",
        path=str(config_path),
        service_count=len(infra_config.services),
    )

    if output_json:
        warnings = (
            []
            if "infrastructure" in project_config
            else ["No infrastructure section in phlo.yaml; using defaults."]
        )
        click.echo(
            json_envelope(
                data={
                    "valid": True,
                    "path": str(config_path),
                    "service_count": len(infra_config.services),
                },
                warnings=warnings,
            )
        )
        return

    if "infrastructure" not in project_config:
        logger.info("config_validate_infrastructure_missing", path=str(config_path))
        console.print("[yellow]Warning: No infrastructure section in phlo.yaml[/yellow]")
        console.print(
            "Using default infrastructure configuration. Run [cyan]phlo config upgrade[/cyan] to add it.\n"
        )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Details")

    table.add_row("Schema Validation", "✓ Valid", "All fields conform to schema")
    table.add_row(
        "Services Defined",
        "✓ Valid",
        f"{len(infra_config.services)} services configured",
    )
    table.add_row(
        "Naming Pattern",
        "✓ Valid",
        f"Pattern: {infra_config.container_naming_pattern}",
    )
    table.add_row(
        "Network Config",
        "✓ Valid",
        f"Driver: {infra_config.network.driver}",
    )

    console.print(table)
    console.print("\n[green]✓ Configuration is valid![/green]\n")


@config.command("upgrade")
@click.option("--force", is_flag=True, help="Overwrite existing infrastructure section")
def upgrade(force: bool):
    """Add infrastructure section to existing phlo.yaml.

    \b
    Examples:
      phlo config upgrade
      phlo config upgrade --force
    """
    config_path = Path.cwd() / "phlo.yaml"

    if not config_path.exists():
        logger.warning("config_upgrade_file_missing", path=str(config_path))
        error_console.print("[red]Error: No phlo.yaml found in current directory[/red]")
        error_console.print("Run [cyan]phlo services init[/cyan] to create a new project")
        sys.exit(1)

    project_config = _load_phlo_yaml(config_path)

    if "infrastructure" in project_config and not force:
        logger.warning(
            "config_upgrade_skipped", path=str(config_path), reason="infrastructure_exists"
        )
        console.print("[yellow]Infrastructure section already exists in phlo.yaml[/yellow]")
        error_console.print("Use --force to overwrite")
        sys.exit(1)

    default_infra = InfrastructureConfig()
    project_config["infrastructure"] = default_infra.model_dump(exclude_none=False, mode="python")

    with config_path.open("w") as f:
        yaml.dump(
            project_config,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    console.print(f"[green]✓ Updated {config_path}[/green]")
    console.print("Added infrastructure section\n")

    clear_config_cache()
    logger.info("config_upgrade_succeeded", path=str(config_path), force=force)

    console.print("Next steps:")
    console.print("  1. Review the infrastructure section in phlo.yaml")
    console.print("  2. Run [cyan]phlo config validate[/cyan] to verify")
    console.print("  3. Customize service names or container patterns if needed")
