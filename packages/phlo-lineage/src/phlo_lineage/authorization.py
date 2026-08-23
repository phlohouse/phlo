"""CLI authorization table for the phlo-lineage surface.

All lineage.column.* and export/impact/show/status commands are reads;
the single mutation is lineage.column.import-dbt, which writes the
"lineage_store" resource (action lineage.import). The adapter is built
once at import time and served through get_lineage_adapter().
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-lineage"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["lineage.column.import-dbt"])
READ_COMMANDS: frozenset[str] = frozenset(
    [
        "lineage.column.downstream",
        "lineage.column.upstream",
        "lineage.export",
        "lineage.impact",
        "lineage.show",
        "lineage.status",
    ]
)
COMMAND_RESOURCE_MAP: dict[str, str] = {"lineage.column.import-dbt": "lineage_store"}
COMMAND_ACTION_MAP: dict[str, str] = {"lineage.column.import-dbt": "lineage.import"}

LineageSurfaceAdapter = cli_surface_adapter_class(
    "LineageSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="lineage",
    default_resource_prefix="lineage",
)


def get_lineage_adapter() -> CliSurfaceAdapter:
    """Return the process-wide CLI authorization adapter for the phlo-lineage surface."""
    return LineageSurfaceAdapter.get_instance()
