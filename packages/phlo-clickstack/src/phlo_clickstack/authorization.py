"""Authorization table for the phlo-clickstack CLI surface.

Declares which commands mutate state and maps every command onto the resource
and action names evaluated by the shared CLI surface adapter.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-clickstack"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["clickstack.query"])
READ_COMMANDS: frozenset[str] = frozenset([])
COMMAND_RESOURCE_MAP: dict[str, str] = {"clickstack.query": "dataset"}
COMMAND_ACTION_MAP: dict[str, str] = {"clickstack.query": "dataset.write"}

ClickStackSurfaceAdapter = cli_surface_adapter_class(
    "ClickStackSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="clickstack",
    default_resource_prefix="clickstack",
)


def get_adapter() -> CliSurfaceAdapter:
    """Return the shared ClickStackSurfaceAdapter instance for this surface."""
    return ClickStackSurfaceAdapter.get_instance()
