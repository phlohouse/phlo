"""CLI authorization table for the phlo-clickhouse surface.

clickhouse.query is a mutation on the "dataset" resource (action
dataset.write); clickhouse.status is read-only. The adapter is built
once at import time and served through get_adapter().
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-clickhouse"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["clickhouse.query"])
READ_COMMANDS: frozenset[str] = frozenset(["clickhouse.status"])
COMMAND_RESOURCE_MAP: dict[str, str] = {"clickhouse.query": "dataset"}
COMMAND_ACTION_MAP: dict[str, str] = {"clickhouse.query": "dataset.write"}

ClickHouseSurfaceAdapter = cli_surface_adapter_class(
    "ClickHouseSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="clickhouse",
    default_resource_prefix="clickhouse",
)


def get_adapter() -> CliSurfaceAdapter:
    """Return the shared phlo-clickhouse CLI authorization adapter instance."""
    return ClickHouseSurfaceAdapter.get_instance()
