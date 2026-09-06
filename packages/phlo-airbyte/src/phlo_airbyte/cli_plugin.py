"""CLI plugin registration for the Airbyte package."""

from __future__ import annotations

from phlo.plugins.base import cli_command_plugin_class

from phlo_airbyte.cli import airbyte_group

AirbyteCliPlugin = cli_command_plugin_class(
    "AirbyteCliPlugin",
    name="airbyte",
    version="0.1.0",
    description="Airbyte connection and sync commands",
    commands=[airbyte_group],
)
