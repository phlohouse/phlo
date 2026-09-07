"""Plugin search command.

Merges installed plugins with remote registry results under type, tag, and
free-text query filters; installed entries take precedence over same-named
registry hits. Renders as a rich table or machine-readable JSON.
"""

from __future__ import annotations

import click
from rich.table import Table

from phlo.cli.commands.plugin.utils import (
    PLUGIN_TYPE_CHOICES,
    collect_installed_plugins,
    console,
    registry_plugin_to_dict,
    registry_type_for_cli,
)
from phlo.cli.contract import PhloCommand
from phlo.cli.output import json_envelope, user_error
from phlo.logging import get_logger
from phlo.plugins.registry_client import search_plugins

logger = get_logger(__name__)


def _matches_installed_plugin(
    plugin: dict,
    *,
    query: str | None,
    plugin_type: str | None,
    tags: tuple[str, ...],
) -> bool:
    """Return true when an installed plugin matches search filters."""
    if plugin_type and plugin.get("type") != plugin_type:
        return False
    if tags:
        plugin_tags = {str(tag).lower() for tag in plugin.get("tags", [])}
        if not all(tag.lower() in plugin_tags for tag in tags):
            return False
    if not query:
        return True
    needle = query.lower()
    haystack = " ".join(
        str(plugin.get(key, "")) for key in ("name", "type", "description", "package", "author")
    ).lower()
    haystack_tags = " ".join(str(tag) for tag in plugin.get("tags", [])).lower()
    return needle in haystack or needle in haystack_tags


@click.command(name="search", cls=PhloCommand)
@click.argument("query", required=False)
@click.option(
    "--type",
    "plugin_type",
    type=click.Choice(PLUGIN_TYPE_CHOICES),
    help="Filter by plugin type",
)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Filter by one or more tags",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
def search_cmd(
    query: str | None, plugin_type: str | None, tags: tuple[str, ...], output_json: bool
):
    """Search plugin registry."""
    try:
        logger.info(
            "plugin_search_started",
            has_query=query is not None,
            plugin_type=plugin_type,
            tag_count=len(tags),
            output_json=output_json,
        )
        if plugin_type:
            plugin_type = registry_type_for_cli(plugin_type)
        installed_type = "all"
        installed_results = [
            plugin
            for plugin in collect_installed_plugins(installed_type)
            if _matches_installed_plugin(
                plugin,
                query=query,
                plugin_type=plugin_type,
                tags=tags,
            )
        ]

        results = search_plugins(
            query=query,
            plugin_type=plugin_type,
            tags=list(tags) if tags else None,
        )

        output_by_key = {(plugin["name"], plugin["type"]): plugin for plugin in installed_results}
        for plugin in results:
            payload = registry_plugin_to_dict(plugin)
            output_by_key.setdefault((payload["name"], payload["type"]), payload)
        output = list(output_by_key.values())

        if output_json:
            click.echo(json_envelope(data=output))
            logger.info("plugin_search_succeeded", result_count=len(output), output_json=True)
            return

        if not output:
            console.print("No plugins found.")
            logger.info("plugin_search_succeeded", result_count=0, output_json=False)
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Version", style="yellow")
        table.add_column("Package", style="white")
        table.add_column("Verified", style="blue")

        for plugin in output:
            table.add_row(
                str(plugin.get("name", "")),
                str(plugin.get("type", "")),
                str(plugin.get("version", "")),
                str(plugin.get("package") or plugin.get("name", "")),
                "yes" if plugin.get("verified") else "no",
            )

        console.print(table)
        logger.info("plugin_search_succeeded", result_count=len(output), output_json=False)

    except click.ClickException:
        raise
    except Exception as e:
        logger.exception(
            "plugin_search_failed",
            has_query=query is not None,
            plugin_type=plugin_type,
            tag_count=len(tags),
            output_json=output_json,
        )
        raise user_error(
            "Error searching registry.",
            reason_code="plugin_search_failed",
            run="phlo plugin search --help",
        ) from e
