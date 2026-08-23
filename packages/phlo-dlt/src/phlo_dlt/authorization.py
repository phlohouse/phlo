"""CLI authorization table for the phlo-dlt surface.

workflow.create is the only declared command, a mutation on the
"project" resource (action project.create). The adapter is constructed
at import time and obtained via get_dlt_adapter().
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-dlt"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["workflow.create"])
READ_COMMANDS: frozenset[str] = frozenset([])
COMMAND_RESOURCE_MAP: dict[str, str] = {"workflow.create": "project"}
COMMAND_ACTION_MAP: dict[str, str] = {"workflow.create": "project.create"}

DltSurfaceAdapter = cli_surface_adapter_class(
    "DltSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="dlt",
    default_resource_prefix="dlt",
)


def get_dlt_adapter() -> CliSurfaceAdapter:
    """Return the shared phlo-dlt CLI authorization adapter instance."""
    return DltSurfaceAdapter.get_instance()
