"""Authorization table for the phlo-polaris CLI surface.

Classifies catalog administration commands as mutating or read-only and maps
each onto catalog resources and actions for the shared CLI surface adapter.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-polaris-cli"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["bootstrap.run", "migrate.import"])
READ_COMMANDS: frozenset[str] = frozenset(["status.read", "catalogs.list", "migrate.plan"])
COMMAND_RESOURCE_MAP: dict[str, str] = {
    "bootstrap.run": "catalog",
    "migrate.import": "catalog",
    "migrate.plan": "catalog",
    "catalogs.list": "catalog",
    "status.read": "catalog",
}
COMMAND_ACTION_MAP: dict[str, str] = {
    "bootstrap.run": "catalog.manage",
    "migrate.import": "catalog.manage",
    "migrate.plan": "catalog.read",
    "catalogs.list": "catalog.read",
    "status.read": "catalog.read",
}

PolarisCliSurfaceAdapter = cli_surface_adapter_class(
    "PolarisCliSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="polaris",
    default_resource_prefix="polaris",
)


def get_polaris_cli_adapter() -> CliSurfaceAdapter:
    """Return the process-wide Polaris CLI authorization adapter instance."""
    return PolarisCliSurfaceAdapter.get_instance()
