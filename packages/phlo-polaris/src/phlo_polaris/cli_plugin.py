"""CLI plugin registration for the Polaris package."""

from __future__ import annotations

from phlo.plugins.base import cli_command_plugin_class

from phlo_polaris.cli import polaris_group

PolarisCliPlugin = cli_command_plugin_class(
    "PolarisCliPlugin",
    name="polaris",
    version="0.1.0",
    description="Polaris catalog administration and migration commands",
    commands=[polaris_group],
)
