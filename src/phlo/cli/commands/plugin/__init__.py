"""Plugin management commands.

Assembles the `phlo plugin` group from its sibling modules (list, info,
check, search, install, update, create) and exports it for CLI wiring.
"""

from __future__ import annotations

import click

from phlo.cli.commands.plugin.check import check_cmd
from phlo.cli.commands.plugin.create import create_cmd
from phlo.cli.commands.plugin.info import info_cmd
from phlo.cli.commands.plugin.install import install_cmd
from phlo.cli.commands.plugin.list import list_cmd
from phlo.cli.commands.plugin.search import search_cmd
from phlo.cli.commands.plugin.update import update_cmd
from phlo.cli.contract import PhloGroup


@click.group(name="plugin", cls=PhloGroup)
def plugin_group():
    """Manage Phlo plugins."""


# Register commands
plugin_group.add_command(list_cmd)
plugin_group.add_command(info_cmd)
plugin_group.add_command(check_cmd)
plugin_group.add_command(search_cmd)
plugin_group.add_command(install_cmd)
plugin_group.add_command(update_cmd)
plugin_group.add_command(create_cmd)

__all__ = ["plugin_group"]
