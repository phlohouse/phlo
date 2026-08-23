"""Sling CLI plugin registration.

Declares the Sling replication CLI command group through the shared CLI plugin
factory; registration metadata only.

Loaded through the phlo plugins.cli entry point at startup rather than imported directly.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class
from phlo_sling.cli_commands import sling_group


SlingCliPlugin = cli_command_plugin_class(
    "SlingCliPlugin",
    name="sling",
    version="0.1.0",
    description="Sling replication CLI commands for Phlo",
    commands=[sling_group],
)
