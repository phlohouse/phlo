"""Trino CLI plugin registration.

Exposes the trino command group as a cli-command plugin so the phlo-trino
package contributes its commands through plugin discovery rather than a
core-CLI import.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Contributes the phlo_trino.cli trino command group through phlo.plugins.base.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class

from phlo_trino.cli import trino_group


TrinoCliPlugin = cli_command_plugin_class(
    "TrinoCliPlugin",
    name="trino",
    version="0.1.0",
    description="CLI commands for Trino query access",
    commands=[trino_group],
)
