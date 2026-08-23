"""CLI command plugin classes.

This module defines plugin types for extending the Phlo CLI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import click

from phlo.plugins.base.plugin import Plugin, PluginMetadata


class CliCommandPlugin(Plugin, ABC):
    """Base class for CLI command plugins.

    These plugins contribute Click commands/groups to the `phlo` CLI at runtime.

    Intended use:
    - Capability packages (e.g., `phlo-nessie`, `phlo-openmetadata`) provide their own CLI surface.
    - `phlo` core stays lightweight and only provides the CLI glue + shared utilities.
    """

    @abstractmethod
    def get_cli_commands(self) -> list[click.Command]:
        """Return Click commands/groups to register on the root CLI."""
        raise NotImplementedError


def cli_command_plugin_class(
    class_name: str,
    *,
    name: str,
    version: str,
    description: str,
    commands: list[click.Command],
) -> type[CliCommandPlugin]:
    """Create a CLI plugin class from static metadata and Click commands."""
    metadata = PluginMetadata(name=name, version=version, description=description)

    class DeclarativeCliCommandPlugin(CliCommandPlugin):
        @property
        def metadata(self) -> PluginMetadata:
            """Return the plugin metadata captured at class creation."""
            return metadata

        def get_cli_commands(self) -> list[click.Command]:
            """Return the Click commands captured at class creation."""
            return list(commands)

    DeclarativeCliCommandPlugin.__name__ = class_name
    DeclarativeCliCommandPlugin.__qualname__ = class_name
    return DeclarativeCliCommandPlugin
