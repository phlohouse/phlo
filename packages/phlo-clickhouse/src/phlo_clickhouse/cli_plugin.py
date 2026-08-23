"""Register the ClickHouse CLI command group as a phlo CLI plugin.

ClickHouseCliPlugin is built via cli_command_plugin_class so plugin discovery
exposes the ClickHouse commands through the phlo CLI.
Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; exposes the command group from phlo_clickhouse.cli.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class

from phlo_clickhouse.cli import clickhouse_group


ClickHouseCliPlugin = cli_command_plugin_class(
    "ClickHouseCliPlugin",
    name="clickhouse",
    version="0.1.0",
    description="CLI commands for ClickHouse data plane access",
    commands=[clickhouse_group],
)
