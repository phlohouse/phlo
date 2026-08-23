"""Register the Pandera CLI command group as a phlo CLI plugin.

PanderaCliPlugin is built via cli_command_plugin_class so plugin discovery
exposes the Pandera commands through the phlo CLI.

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class
from phlo_pandera.cli_schema import schema
from phlo_pandera.cli_validate import validate_schema, validate_workflow


PanderaCliPlugin = cli_command_plugin_class(
    "PanderaCliPlugin",
    name="quality",
    version="0.1.0",
    description="Quality and schema CLI commands",
    commands=[schema, validate_schema, validate_workflow],
)
