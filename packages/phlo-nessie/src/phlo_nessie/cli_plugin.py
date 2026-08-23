"""Nessie CLI plugin registration.

Wires the catalog and branch command groups into the plugin system via
cli_command_plugin_class; owns no logic beyond registration.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the nessie catalog and branch command groups via phlo.plugins.base.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class
from phlo_nessie.cli_branch import branch
from phlo_nessie.cli_catalog import catalog


NessieCliPlugin = cli_command_plugin_class(
    "NessieCliPlugin",
    name="nessie",
    version="0.1.0",
    description="Nessie catalog and branch management commands",
    commands=[catalog, branch],
)
