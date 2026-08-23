"""Plugin update command.

Compares installed plugins against the registry and upgrades them with
pip. Read-only modes (--json, --dry-run) bypass mutation authorization;
the actual update requires the "plugin.update" mutation. Per-plugin
failures are collected, reported, and fail the command with exit 1
after every remaining update has been attempted.
"""

from __future__ import annotations

import json
import sys

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.plugin.utils import console, find_available_updates, run_pip
from phlo.logging import get_logger
from phlo.plugins.registry_client import list_registry_plugins

logger = get_logger(__name__)


@click.command(name="update")
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Outputs available updates as JSON and does not apply them",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Show available updates without applying them",
)
@require_mutation_authorization(
    "plugin.update",
    when=lambda params: not params.get("output_json") and not params.get("dry_run"),
)
def update_cmd(output_json: bool, dry_run: bool):
    """Update installed plugins based on registry versions."""
    try:
        logger.info("plugin_update_started", output_json=output_json, dry_run=dry_run)
        registry_plugins = list_registry_plugins()
        updates = find_available_updates(registry_plugins)

        if output_json:
            console.print(json.dumps(updates, indent=2))
            logger.info(
                "plugin_update_succeeded",
                output_json=True,
                dry_run=dry_run,
                update_count=len(updates),
            )
            return

        if dry_run:
            if not updates:
                console.print("No plugin updates available.")
                logger.info(
                    "plugin_update_succeeded",
                    output_json=False,
                    dry_run=True,
                    update_count=0,
                )
                return

            console.print("Updates available:")
            for update in updates:
                console.print(
                    f"  {update['name']}: {update['installed_version']} → {update['available_version']}"
                )
            logger.info(
                "plugin_update_succeeded",
                output_json=False,
                dry_run=True,
                update_count=len(updates),
            )
            return

        if not updates:
            console.print("No plugin updates available.")
            logger.info("plugin_update_succeeded", output_json=False, dry_run=False, update_count=0)
            return

        console.print("Updates available:")
        for update in updates:
            console.print(
                f"  {update['name']}: {update['installed_version']} → {update['available_version']}"
            )

        failures: list[dict[str, str]] = []
        for update in updates:
            try:
                run_pip(
                    ["install", "--upgrade", f"{update['package']}=={update['available_version']}"]
                )
            except Exception as exc:
                logger.warning(
                    "plugin_update_package_failed",
                    package=update["package"],
                    target_version=update["available_version"],
                    exc_info=True,
                )
                console.print(
                    "[red]Failed to update "
                    f"{update['package']}=={update['available_version']}: {exc}[/red]"
                )
                failures.append(
                    {
                        "package": update["package"],
                        "version": update["available_version"],
                    }
                )
                continue

        if failures:
            logger.error(
                "plugin_update_failed", failure_count=len(failures), update_count=len(updates)
            )
            failed_list = ", ".join(f"{item['package']}=={item['version']}" for item in failures)
            console.print(f"[red]Failed updates: {failed_list}[/red]")
            sys.exit(1)

        logger.info(
            "plugin_update_succeeded", output_json=False, dry_run=False, update_count=len(updates)
        )
        console.print("[green]✓ Plugins updated[/green]")
    except Exception as e:
        logger.exception("plugin_update_failed", output_json=output_json, dry_run=dry_run)
        console.print(f"[red]Error updating plugins: {e}[/red]")
        sys.exit(1)
