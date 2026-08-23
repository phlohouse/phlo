"""phlo_dbt CLI authorization table.

Declares which dbt CLI commands mutate versus read and maps them to
resource/action pairs. The adapter is built once by
cli_surface_adapter_class and handed out as a singleton.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-dbt"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["dbt.publishing.scaffold", "dbt.run"])
READ_COMMANDS: frozenset[str] = frozenset(["dbt.compile", "dbt.test"])
COMMAND_RESOURCE_MAP: dict[str, str] = {"dbt.run": "dataset", "dbt.publishing.scaffold": "project"}
COMMAND_ACTION_MAP: dict[str, str] = {
    "dbt.run": "dataset.write",
    "dbt.publishing.scaffold": "project.create",
}

DbtSurfaceAdapter = cli_surface_adapter_class(
    "DbtSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="dbt",
    default_resource_prefix="dbt",
)


def get_dbt_adapter() -> CliSurfaceAdapter:
    """Return the shared DbtSurfaceAdapter instance for this surface."""
    return DbtSurfaceAdapter.get_instance()
