"""Authorization surface table for the phlo-postgres CLI.

Declares which postgres commands mutate state plus their dataset resources and
required actions; the shared CLI surface adapter enforces these mappings.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-postgres-cli"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(
    ["postgres", "postgres.dump", "postgres.query", "postgres.restore", "postgres.vacuum"]
)
READ_COMMANDS: frozenset[str] = frozenset([])
COMMAND_RESOURCE_MAP: dict[str, str] = {
    "postgres.query": "dataset",
    "postgres.dump": "dataset",
    "postgres.restore": "dataset",
    "postgres.vacuum": "dataset",
    "postgres": "dataset",
}
COMMAND_ACTION_MAP: dict[str, str] = {
    "postgres.query": "dataset.query",
    "postgres.dump": "dataset.manage",
    "postgres.restore": "dataset.manage",
    "postgres.vacuum": "dataset.manage",
    "postgres": "dataset.query",
}

PostgresCliSurfaceAdapter = cli_surface_adapter_class(
    "PostgresCliSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="postgres",
    default_resource_prefix="postgres",
)


def get_postgres_cli_adapter() -> CliSurfaceAdapter:
    """Return the shared Postgres CLI surface adapter instance."""
    return PostgresCliSurfaceAdapter.get_instance()
