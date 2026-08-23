"""Clickstack CLI plugin registration.

Registers the clickstack command group with the plugin system via
cli_command_plugin_class; owns no logic beyond registration.

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class

from phlo_clickstack.cli import clickstack_group


ClickStackCliPlugin = cli_command_plugin_class(
    "ClickStackCliPlugin",
    name="clickstack",
    version="0.1.0",
    description="CLI commands for ClickStack query access",
    commands=[clickstack_group],
)
