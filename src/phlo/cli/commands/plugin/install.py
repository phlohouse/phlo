"""Plugin install command.

Resolves a plugin name against the registry to a pinned package spec, installs
it with pip, and reports the result. Treated as a mutation: the whole command
sits behind require_mutation_authorization.
"""

from __future__ import annotations

import sys

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.plugin.utils import collect_installed_plugins, console, run_pip
from phlo.cli.output import json_envelope
from phlo.logging import get_logger
from phlo.plugins.registry_client import get_plugin as get_registry_plugin

logger = get_logger(__name__)


def resolve_install_target(plugin_name: str) -> tuple[str, str]:
    """Resolve plugin name to package spec and display name."""
    if "==" in plugin_name:
        name_part, version_part = plugin_name.split("==", 1)
    else:
        name_part, version_part = plugin_name, None

    registry_plugin = get_registry_plugin(name_part)
    if registry_plugin:
        if version_part:
            package_spec = f"{registry_plugin.package}=={version_part}"
        elif registry_plugin.version:
            package_spec = f"{registry_plugin.package}=={registry_plugin.version}"
        else:
            package_spec = registry_plugin.package
        display_name = f"{registry_plugin.name} ({registry_plugin.package})"
        return package_spec, display_name

    return plugin_name, plugin_name


@click.command(name="install")
@click.argument("plugin_name")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@require_mutation_authorization("plugin.install")
def install_cmd(plugin_name: str, output_json: bool):
    """Install a plugin from the registry (wraps pip)."""
    try:
        name_part = plugin_name.split("==", 1)[0]
        package_spec, display_name = resolve_install_target(plugin_name)
        logger.info(
            "plugin_install_started",
            plugin_name=plugin_name,
            package_spec=package_spec,
        )
        if not output_json:
            console.print(f"Installing {display_name}...")
        run_pip(["install", package_spec])
        installed = collect_installed_plugins("all")
        maybe_installed = [
            plugin
            for plugin in installed
            if plugin["name"] == name_part or package_spec.startswith(plugin["name"])
        ]
        logger.info(
            "plugin_install_succeeded",
            plugin_name=plugin_name,
            package_spec=package_spec,
        )
        warnings = []
        for plugin in maybe_installed:
            missing_capabilities = plugin.get("missing_capabilities") or []
            if missing_capabilities:
                warnings.append(
                    f"Installed plugin has unmet capabilities: {plugin['name']} -> {', '.join(missing_capabilities)}"
                )
        if output_json:
            click.echo(
                json_envelope(
                    data={
                        "plugin_name": plugin_name,
                        "package_spec": package_spec,
                        "display_name": display_name,
                        "installed_plugins": maybe_installed,
                    },
                    warnings=warnings,
                )
            )
            return

        console.print(f"[green]✓ Installed {display_name}[/green]")
        for warning in warnings:
            console.print(f"[yellow]{warning}[/yellow]")
    except Exception as e:
        logger.exception("plugin_install_failed", plugin_name=plugin_name)
        console.print(f"[red]Error installing plugin: {e}[/red]")
        sys.exit(1)
