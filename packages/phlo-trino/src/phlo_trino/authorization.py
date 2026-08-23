"""Authorization surface table for the phlo-trino CLI.

Declares the trino query commands as mutations on dataset resources; the shared
CLI surface adapter enforces these mappings.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-trino-cli"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["trino", "trino.query"])
READ_COMMANDS: frozenset[str] = frozenset([])
COMMAND_RESOURCE_MAP: dict[str, str] = {"trino.query": "dataset", "trino": "dataset"}
COMMAND_ACTION_MAP: dict[str, str] = {"trino.query": "dataset.query", "trino": "dataset.query"}

TrinoCliSurfaceAdapter = cli_surface_adapter_class(
    "TrinoCliSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="trino",
    default_resource_prefix="trino",
)


def get_trino_cli_adapter() -> CliSurfaceAdapter:
    """Return the shared Trino CLI surface adapter instance."""
    return TrinoCliSurfaceAdapter.get_instance()
