"""Register the alerts CLI command group as a phlo CLI plugin.

AlertingCliPlugin is built via cli_command_plugin_class so plugin discovery
exposes the alerts click group through the phlo CLI.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Registers the alerts command group from phlo_alerting.cli_alerts via phlo.plugins.base.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class
from phlo_alerting.cli_alerts import alerts_group


AlertingCliPlugin = cli_command_plugin_class(
    "AlertingCliPlugin",
    name="alerts",
    version="0.1.0",
    description="Alerting CLI commands",
    commands=[alerts_group],
)
