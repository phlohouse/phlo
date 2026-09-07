"""
Phlo CLI Main Entry Point

Provides command-line interface for Phlo workflows.
Entry module for the phlo console script; imported by the phlo-mcp server and
covered extensively by the CLI and integration test suites.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import cast

import click

import phlo.cli._init_discovery_guard  # noqa: F401
import phlo.cli._warning_filters  # noqa: F401
from phlo.cli._init_discovery_guard import _root_command_name, is_init_command_invocation
from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.commands import commands_cmd
from phlo.cli.commands.doctor import doctor_cmd
from phlo.cli.commands.support import support_group
from phlo.cli.contract import PhloGroup
from phlo.cli.output import json_envelope, user_error
from phlo.cli.templates import TemplateRenderContext, get_template
from phlo.cli.templates import list_templates as get_project_templates
from phlo.cli.templates.registry import missing_required_packages
from phlo.logging import get_logger, setup_logging
from phlo.plugins.base.cli import CliCommandPlugin

logger = get_logger(__name__, service="phlo-cli")


def _is_doctor_invocation(argv: list[str]) -> bool:
    for token in argv[1:]:
        if token == "--":
            return False
        if token in {"--help", "-h", "--version"}:
            return False
        if token.startswith("-"):
            continue
        return token == "doctor"
    return False


# Startup guard computed from argv before any heavy imports. `phlo doctor`,
# `phlo support`, and `phlo init` must run in minimal installs where the
# audit/plugin machinery below cannot be imported, so those invocations skip
# it entirely instead of failing at module import time.
_DOCTOR_INVOCATION = _is_doctor_invocation(sys.argv)
_SUPPORT_INVOCATION = _root_command_name(sys.argv) == "support"
_INIT_INVOCATION = is_init_command_invocation(sys.argv)

if not (_DOCTOR_INVOCATION or _SUPPORT_INVOCATION):
    from phlo.cli.commands.audit import audit_group
    from phlo.cli.commands.authz import authz_group
    from phlo.cli.commands.compliance import compliance_group
    from phlo.cli.commands.dataset import dataset_group
    from phlo.cli.commands.governance import governance_group
    from phlo.cli.commands.metrics import metrics_group
    from phlo.cli.commands.migrate import migrate_group
    from phlo.cli.commands.operations import operations_group
    from phlo.cli.commands.plugin import plugin_group
    from phlo.cli.commands.schema_migrate import schema_migrate_group
    from phlo.cli.commands.schema_registry_cli import contracts
    from phlo.cli.commands.services import _register_commands as _register_service_commands
    from phlo.cli.commands.services import services_group
    from phlo.cli.commands.services.logs import logs_cmd
    from phlo.cli.commands.workflow import workflow_group
    from phlo.cli.config import config
    from phlo.cli.env import env


@click.group(cls=PhloGroup)
@click.version_option(version=version("phlo"), prog_name="phlo")
@click.option("--quiet", is_flag=True, help="Reduce non-essential CLI output.")
@click.option("--no-color", is_flag=True, help="Disable colorized terminal output.")
def cli(quiet: bool, no_color: bool) -> None:
    """
    Phlo - Modern Data Lakehouse Framework

    Build and inspect lakehouse workflows locally with minimal boilerplate.

    Documentation: https://github.com/iamgp/phlo
    """
    if quiet:
        os.environ["PHLO_QUIET"] = "1"
    if no_color:
        os.environ["NO_COLOR"] = "1"
        os.environ["CLICOLOR"] = "0"
    setup_logging()


cli.add_command(commands_cmd)
cli.add_command(doctor_cmd)
cli.add_command(support_group)

if not (_DOCTOR_INVOCATION or _SUPPORT_INVOCATION):
    cli.add_command(audit_group)
    cli.add_command(logs_cmd)
    cli.add_command(services_group)
    cli.add_command(operations_group)
    cli.add_command(workflow_group)
    cli.add_command(plugin_group)
    cli.add_command(schema_migrate_group)
    cli.add_command(migrate_group)
    cli.add_command(metrics_group)
    cli.add_command(contracts)
    cli.add_command(config)
    cli.add_command(env)
    cli.add_command(authz_group)
    cli.add_command(compliance_group)
    cli.add_command(governance_group)
    cli.add_command(dataset_group)


def _load_cli_plugin_commands() -> None:
    from phlo.plugins.discovery import discover_plugins, get_global_registry

    logger.debug("cli_plugin_discovery_started")
    discover_plugins(plugin_type="cli_command", auto_register=True, failure_level="debug")
    registry = get_global_registry()
    added_count = 0
    for name in registry.list("cli_command"):
        plugin = registry.get("cli_command", name)
        if plugin is None:
            logger.warning("cli_plugin_missing_in_registry", plugin_name=name)
            continue
        plugin = cast(CliCommandPlugin, plugin)
        for command in plugin.get_cli_commands():
            if command.name is None or command.name in cli.commands:
                logger.debug("cli_command_skipped", plugin_name=name, command_name=command.name)
                continue
            cli.add_command(command)
            added_count += 1
            logger.debug("cli_command_added", plugin_name=name, command_name=command.name)
    logger.debug("cli_plugin_discovery_completed", command_count=added_count)


if not (_DOCTOR_INVOCATION or _SUPPORT_INVOCATION):
    _register_service_commands()
if not (_DOCTOR_INVOCATION or _SUPPORT_INVOCATION or _INIT_INVOCATION):
    _load_cli_plugin_commands()


@cli.command()
@click.argument("asset_name", required=False)
@click.option("--local", is_flag=True, help="Run tests locally without Docker")
@click.option("--coverage", is_flag=True, help="Generate coverage report")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.option("-m", "--marker", help="Run tests with specific pytest marker")
def test(
    asset_name: str | None,
    local: bool,
    coverage: bool,
    verbose: bool,
    marker: str | None,
):
    """
    Run tests for Phlo workflows.

    Examples:
        phlo test                          # Run all tests
        phlo test weather_observations     # Run tests for specific asset
        phlo test --local                  # Run without Docker
        phlo test --coverage               # Generate coverage report
        phlo test -m integration           # Run integration tests only
    """
    click.echo("Running Phlo tests...\n")

    # Build pytest command
    pytest_args = ["pytest"]

    if asset_name:
        # Run tests for specific asset
        test_file = f"tests/test_{asset_name}.py"
        if Path(test_file).exists():
            pytest_args = ["pytest", test_file]
        else:
            logger.warning("test_file_not_found", test_file=test_file)
            click.echo(f"Error: Test file not found: {test_file}", err=True)
            click.echo("\nAvailable test files:", err=True)
            for f in Path("tests").glob("test_*.py"):
                click.echo(f"  - {f.name}", err=True)
            sys.exit(1)

    if marker and local:
        pytest_args.extend(["-m", f"({marker}) and not integration"])
    elif marker:
        pytest_args.extend(["-m", marker])
    elif local:
        pytest_args.extend(["-m", "not integration"])

    if local:
        os.environ["PHLO_TEST_LOCAL"] = "1"
        click.echo("Local test mode enabled (PHLO_TEST_LOCAL=1)\n")

    if verbose:
        pytest_args.append("-v")

    if coverage:
        pytest_args.extend(["--cov=phlo", "--cov-report=html", "--cov-report=term"])

    command = pytest_args
    if shutil.which("uv") and Path("pyproject.toml").exists():
        command = ["uv", "run", *pytest_args]

    # Run pytest
    try:
        result = subprocess.run(command, check=False)
        if result.returncode == 5 and asset_name is None:
            click.echo("\nNo tests were collected. Add tests under tests/ to enable this gate.")
            sys.exit(0)
        sys.exit(result.returncode)
    except FileNotFoundError:
        logger.error("pytest_binary_not_found")
        click.echo("Error: pytest not found. Install with: pip install pytest", err=True)
        sys.exit(1)


def _display_created_structure(project_dir: Path, selected_template) -> None:
    click.echo("Created structure:")
    click.echo(f"  {project_dir}/")
    click.echo("  ├── phlo.yaml            # Project configuration")
    click.echo("  ├── pyproject.toml       # Project dependencies")
    click.echo("  ├── .env.example         # Local secrets template")
    click.echo("  ├── .gitignore")
    click.echo("  ├── README.md")
    click.echo("  ├── workflows/           # Workflow definitions")
    click.echo("  └── tests/               # Workflow tests")
    common_paths = {
        "phlo.yaml",
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        "README.md",
        "workflows/__init__.py",
        "tests/__init__.py",
    }
    extra_paths = tuple(
        path for path in selected_template.metadata.generated_paths if path not in common_paths
    )
    if extra_paths:
        click.echo("  Template additions:")
        for path in extra_paths:
            click.echo(f"    - {path}")


def _available_service_count() -> int:
    """Return discovered service count, tolerating minimal installs."""
    try:
        from phlo.plugins.discovery import ServiceDiscovery

        return len(ServiceDiscovery().discover())
    except Exception:
        return 0


def _render_next_steps(selected_template) -> list[str]:
    """Tailor project next steps to the packages installed in this environment."""
    template_steps = list(selected_template.metadata.next_steps)
    steps = ["uv pip install -e ."]

    service_count = _available_service_count()
    template_service_start_steps = [
        step for step in template_steps if step.startswith("phlo services start")
    ]
    if service_count == 0:
        install_step = 'Install service plugins: uv pip install "phlo[defaults]"'
        if install_step not in steps:
            steps.append(install_step)
    else:
        steps.append("phlo services init")
        if template_service_start_steps:
            steps.extend(template_service_start_steps)
        else:
            steps.append("phlo services start")
        steps.append("phlo doctor")

    for step in template_steps:
        if step == "phlo services init" or step.startswith("phlo services start"):
            continue
        if step == "phlo workflow create" and importlib.util.find_spec("phlo_dlt") is None:
            install_step = 'Install workflow plugins: uv pip install "phlo[defaults]"'
            if install_step not in steps:
                steps.append(install_step)
            continue
        steps.append(step)

    return list(dict.fromkeys(steps))


@cli.command("init")
@click.argument("project_name", required=False)
@click.option(
    "--template",
    default="minimal",
    show_default=True,
    help="Project template to use",
)
@click.option("--force", is_flag=True, help="Initialize in non-empty directory")
@click.option("--list-templates", is_flag=True, help="List available project templates and exit.")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@require_mutation_authorization("init", when=lambda params: not params.get("list_templates"))
def init(
    project_name: str | None,
    template: str,
    force: bool,
    list_templates: bool,
    output_json: bool,
):
    """
    Initialize a new Phlo project.

    Creates a minimal project structure for using Phlo as an installable package.
    Users only need to maintain workflow files, not the entire framework.

    Examples:
        phlo init my-data-project          # Create new project directory
        phlo init . --force                # Initialize in current directory
        phlo init weather-pipeline --template csv-batch
    """
    if list_templates:
        items = [
            {
                "name": item.metadata.name,
                "description": item.metadata.description,
                "required_packages": list(item.metadata.required_packages),
                "generated_paths": list(item.metadata.generated_paths),
                "next_steps": list(item.metadata.next_steps),
            }
            for item in get_project_templates()
        ]
        if output_json:
            click.echo(json_envelope(data={"items": items}))
            return
        for item in get_project_templates():
            packages = ", ".join(item.metadata.required_packages)
            click.echo(f"{item.metadata.name:<20} {item.metadata.description:<36} {packages}")
        return

    if not output_json:
        click.echo("Phlo Project Initializer\n")

    # Determine project directory and metadata-safe project name
    if project_name is None or project_name == ".":
        project_dir = Path.cwd()
        project_metadata_name = project_dir.name
        if not output_json:
            click.echo(f"Initializing in current directory: {project_dir}")
    else:
        requested_path = Path(project_name).expanduser()
        project_dir = (
            requested_path if requested_path.is_absolute() else (Path.cwd() / requested_path)
        )
        project_metadata_name = project_dir.name
        if not output_json:
            click.echo(f"Creating new project: {project_dir}")

    # Check if directory exists and is not empty
    if project_dir.exists() and any(project_dir.iterdir()) and not force:
        raise user_error(
            f"Directory {project_dir} is not empty",
            details=["Use --force to initialize anyway."],
            reason_code="directory_not_empty",
        )

    # Create project structure
    try:
        selected_template = _create_project_structure(project_dir, project_metadata_name, template)
        next_steps = _render_next_steps(selected_template)

        if output_json:
            click.echo(
                json_envelope(
                    data={
                        "project_dir": str(project_dir),
                        "project_name": project_metadata_name,
                        "template": selected_template.metadata.name,
                        "generated_paths": list(selected_template.metadata.generated_paths),
                        "next_steps": (
                            [f"cd {project_dir}", *next_steps]
                            if project_dir != Path.cwd()
                            else next_steps
                        ),
                    }
                )
            )
            return

        click.echo(f"\nSuccessfully initialized Phlo project: {project_dir}\n")
        _display_created_structure(project_dir, selected_template)

        click.echo("\nNext steps:")
        step_number = 1
        if project_dir != Path.cwd():
            click.echo(f"  {step_number}. cd {project_dir}")
            step_number += 1
        for next_step in next_steps:
            click.echo(f"  {step_number}. {next_step}")
            step_number += 1

        click.echo("\nDocumentation: https://github.com/iamgp/phlo")

    except click.ClickException:
        raise
    except Exception as e:
        logger.exception(
            "project_initialization_failed",
            project_dir=str(project_dir),
            error=str(e),
        )
        raise click.ClickException("could not initialize project") from e


def _create_project_structure(project_dir: Path, project_name: str, template: str):
    """Resolve the named project template and validate its required packages."""
    try:
        selected_template = get_template(template)
    except KeyError as exc:
        available = ", ".join(item.metadata.name for item in get_project_templates())
        raise click.ClickException(
            f"Unknown template '{template}'. Available templates: {available}"
        ) from exc
    missing = missing_required_packages(selected_template)
    if missing:
        packages = " ".join(missing)
        raise click.ClickException(
            f"Template '{template}' requires missing package(s): {', '.join(missing)}. "
            f"Install with: uv pip install {packages}"
        )
    selected_template.render(
        TemplateRenderContext(project_dir=project_dir, project_name=project_name)
    )
    return selected_template


def main():
    """Main entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
