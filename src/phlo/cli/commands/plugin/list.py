"""Run the ``phlo plugin list`` command.

Collects installed plugins and optionally registry entries, rendering them as
a table or as JSON; discovery failures exit nonzero.
"""

from __future__ import annotations

import json
import sys

import click

from phlo.cli.commands.plugin.utils import (
    PLUGIN_TYPE_CHOICES,
    collect_installed_plugins,
    collect_registry_plugins,
    console,
    normalize_plugin_type,
    render_plugin_table,
)
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.command(name="list")
@click.option(
    "--type",
    "plugin_type",
    type=click.Choice([*PLUGIN_TYPE_CHOICES, "all"]),
    default="all",
    help="Filter by plugin type",
)
@click.option(
    "--all",
    "include_registry",
    is_flag=True,
    default=False,
    help="Include registry plugins in output",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
def list_cmd(plugin_type: str, include_registry: bool, output_json: bool):
    """List all discovered plugins.

    Examples:
        phlo plugin list                    # List all plugins
        phlo plugin list --type sources     # List source connectors only
        phlo plugin list --json             # Output as JSON
        phlo plugin list --all              # Include registry plugins
    """
    try:
        plugin_type = normalize_plugin_type(plugin_type)
        logger.info(
            "plugin_list_started",
            plugin_type=plugin_type,
            include_registry=include_registry,
            output_json=output_json,
        )
        installed = collect_installed_plugins(plugin_type)
        available = collect_registry_plugins(plugin_type) if include_registry else []

        if output_json:
            output = {"installed": installed}
            if include_registry:
                output["available"] = available
            click.echo(json.dumps(output, indent=2))
            logger.info(
                "plugin_list_succeeded",
                installed_count=len(installed),
                available_count=len(available),
                output_json=True,
            )
            return

        render_plugin_table("Installed", installed)
        if include_registry:
            render_plugin_table("Available", available)
        logger.info(
            "plugin_list_succeeded",
            installed_count=len(installed),
            available_count=len(available),
            output_json=False,
        )

    except Exception as e:
        logger.exception(
            "plugin_list_failed",
            plugin_type=plugin_type,
            include_registry=include_registry,
            output_json=output_json,
        )
        console.print(f"[red]Error listing plugins: {e}[/red]")
        sys.exit(1)
