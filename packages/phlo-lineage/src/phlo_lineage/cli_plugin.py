"""Register the lineage CLI command group as a phlo CLI plugin.

LineageCliPlugin is built via cli_command_plugin_class so plugin discovery
exposes the lineage commands through the phlo CLI.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Exposes the phlo_lineage.cli_lineage command group through phlo.plugins.base.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class
from phlo_lineage.cli_lineage import lineage_group


LineageCliPlugin = cli_command_plugin_class(
    "LineageCliPlugin",
    name="lineage",
    version="0.1.0",
    description="Lineage CLI commands",
    commands=[lineage_group],
)
