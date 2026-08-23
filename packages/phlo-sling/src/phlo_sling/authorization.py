"""phlo_sling CLI authorization table.

Declares which sling CLI commands mutate versus read and maps them to
resource/action pairs. The adapter is built once by
cli_surface_adapter_class and handed out as a singleton.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-sling"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["sling.run"])
READ_COMMANDS: frozenset[str] = frozenset(["sling.conns", "sling.discover"])
COMMAND_RESOURCE_MAP: dict[str, str] = {"sling.run": "replication"}
COMMAND_ACTION_MAP: dict[str, str] = {"sling.run": "replication.execute"}

SlingSurfaceAdapter = cli_surface_adapter_class(
    "SlingSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="sling",
    default_resource_prefix="sling",
)


def get_adapter() -> CliSurfaceAdapter:
    """Return the shared SlingSurfaceAdapter instance for this surface."""
    return SlingSurfaceAdapter.get_instance()
