"""CLI authorization table for the phlo-alerting surface.

Alerting is read-only: every declared command (list, status, test) is a
read; no command maps away from the default "alerting" action/resource
prefixes. The adapter is built once at import time and served through
get_alerting_adapter().
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-alerting"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset([])
READ_COMMANDS: frozenset[str] = frozenset(["alerts.list", "alerts.status", "alerts.test"])
COMMAND_RESOURCE_MAP: dict[str, str] = {}
COMMAND_ACTION_MAP: dict[str, str] = {}

AlertingSurfaceAdapter = cli_surface_adapter_class(
    "AlertingSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="alerting",
    default_resource_prefix="alerting",
)


def get_alerting_adapter() -> CliSurfaceAdapter:
    """Return the shared AlertingSurfaceAdapter instance for this surface."""
    return AlertingSurfaceAdapter.get_instance()
