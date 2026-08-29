"""CLI authorization table for the phlo-pandera surface.

Pandera exposes one durable mutation: ``schema generate`` writes or updates
schema modules. The other schema and workflow validation commands are reads.
The adapter is built once at import time and served through
get_pandera_adapter().
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-pandera"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["schema.generate"])
READ_COMMANDS: frozenset[str] = frozenset(
    [
        "schema.diff",
        "schema.list",
        "schema.show",
        "schema.validate",
        "validate-schema",
        "validate-workflow",
    ]
)
COMMAND_RESOURCE_MAP: dict[str, str] = {"schema.generate": "schema"}
COMMAND_ACTION_MAP: dict[str, str] = {"schema.generate": "schema.generate"}

PanderaSurfaceAdapter = cli_surface_adapter_class(
    "PanderaSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="pandera",
    default_resource_prefix="pandera",
)


def get_pandera_adapter() -> CliSurfaceAdapter:
    """Return the shared authorization adapter for the phlo-pandera surface."""
    return PanderaSurfaceAdapter.get_instance()
