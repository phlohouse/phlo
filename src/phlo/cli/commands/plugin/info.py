"""Plugin info command.

Looks up a plugin across all plugin types by normalized name (package
prefixes such as phlo_plugin_ are stripped for matching). The type is
auto-detected when --type is omitted; an unknown plugin exits non-zero.
"""

from __future__ import annotations

import re

import click

from phlo.cli.commands.plugin.utils import (
    PLUGIN_TYPE_CHOICES,
    PLUGIN_TYPE_MAP,
    console,
    normalize_plugin_type,
)
from phlo.cli.contract import PhloCommand
from phlo.cli.output import json_envelope, user_error
from phlo.logging import get_logger
from phlo.plugins import get_plugin_info, list_plugins

logger = get_logger(__name__)


def _plugin_name_key(value: str) -> str:
    """Normalize plugin/package names for lookup."""
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    for prefix in ("phlo_plugin_", "phlo_"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized


@click.command(name="info", cls=PhloCommand)
@click.argument("plugin_name")
@click.option(
    "--type",
    "plugin_type",
    type=click.Choice(PLUGIN_TYPE_CHOICES),
    help="Plugin type (auto-detected if not specified)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output as JSON",
)
def info_cmd(plugin_name: str, plugin_type: str | None, output_json: bool) -> None:
    """Show detailed plugin information.

    Examples:
        phlo plugin info github              # Show info for 'github' plugin
        phlo plugin info custom --type quality
        phlo plugin info github --json
    """
    try:
        all_plugins = list_plugins()

        plugin_type_provided = bool(plugin_type)
        detected_type: str | None = None

        if not plugin_type_provided:
            for ptype_key, names in all_plugins.items():
                requested = _plugin_name_key(plugin_name)
                if any(requested == _plugin_name_key(name) for name in names):
                    detected_type = ptype_key
                    plugin_name = next(
                        name for name in names if requested == _plugin_name_key(name)
                    )
                    break

            if detected_type is None:
                logger.warning(
                    "plugin_info_not_found", plugin_name=plugin_name, reason="not_detected"
                )
                raise user_error(
                    f"Plugin '{plugin_name}' not found",
                    reason_code="plugin_not_found",
                    run="phlo plugin list",
                )

        if plugin_type_provided:
            display_type = normalize_plugin_type(plugin_type)
            internal_type = PLUGIN_TYPE_MAP[display_type]
        else:
            internal_type = detected_type
            display_type = detected_type

        if internal_type is None or display_type is None:
            logger.warning("plugin_info_not_found", plugin_name=plugin_name, reason="invalid_type")
            raise user_error(
                f"Plugin '{plugin_name}' not found",
                reason_code="plugin_not_found",
                run="phlo plugin list",
            )

        info = get_plugin_info(internal_type, plugin_name)

        if info is None:
            logger.warning(
                "plugin_info_not_found", plugin_name=plugin_name, reason="missing_metadata"
            )
            raise user_error(
                f"Plugin '{plugin_name}' not found",
                reason_code="plugin_not_found",
                run="phlo plugin list",
            )

        if output_json:
            click.echo(json_envelope(data=info))
            return

        # Rich formatted output
        console.print(f"\n[bold cyan]{info['name']}[/bold cyan]")
        console.print(f"Type: {display_type}")
        console.print(f"Version: {info['version']}")

        if info.get("author"):
            console.print(f"Author: {info['author']}")

        if info.get("description"):
            console.print(f"Description: {info['description']}")

        if info.get("license"):
            console.print(f"License: {info['license']}")

        if info.get("homepage"):
            console.print(f"Homepage: {info['homepage']}")

        if info.get("tags"):
            console.print(f"Tags: {', '.join(info['tags'])}")

        if info.get("dependencies"):
            console.print("Dependencies:")
            for dep in info["dependencies"]:
                console.print(f"  - {dep}")

    except SystemExit:
        raise
    except click.ClickException:
        raise
    except Exception as e:
        logger.exception(
            "plugin_info_failed",
            plugin_name=plugin_name,
            plugin_type=plugin_type,
            output_json=output_json,
        )
        raise user_error(
            "Error getting plugin info.",
            reason_code="plugin_info_failed",
            run="phlo plugin info --help",
        ) from e
