"""Authorization table for the phlo-nessie CLI surface.

Classifies branch and catalog commands as mutating or read-only and maps each
onto catalog resources and actions for the shared CLI surface adapter.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-nessie-cli"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["branch.create", "branch.delete", "branch.merge"])
READ_COMMANDS: frozenset[str] = frozenset(
    ["branch.diff", "branch.list", "catalog.describe", "catalog.history", "catalog.tables"]
)
COMMAND_RESOURCE_MAP: dict[str, str] = {
    "branch.create": "catalog",
    "branch.delete": "catalog",
    "branch.merge": "catalog",
    "branch.list": "catalog",
    "branch.diff": "catalog",
    "catalog.tables": "catalog",
    "catalog.describe": "catalog",
    "catalog.history": "catalog",
}
COMMAND_ACTION_MAP: dict[str, str] = {
    "branch.create": "catalog.manage",
    "branch.delete": "catalog.manage",
    "branch.merge": "catalog.manage",
    "branch.list": "catalog.read",
    "branch.diff": "catalog.read",
    "catalog.tables": "catalog.read",
    "catalog.describe": "catalog.read",
    "catalog.history": "catalog.read",
}

NessieCliSurfaceAdapter = cli_surface_adapter_class(
    "NessieCliSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="nessie",
    default_resource_prefix="nessie",
)


def get_nessie_cli_adapter() -> CliSurfaceAdapter:
    """Return the process-wide Nessie CLI authorization adapter instance."""
    return NessieCliSurfaceAdapter.get_instance()
