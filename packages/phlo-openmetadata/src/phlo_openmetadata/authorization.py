"""phlo-openmetadata CLI authorization table.

Declares the CLI surface adapter for RBAC: openmetadata.sync is a mutation
on the metadata_catalog resource (action metadata.sync) and requires
authorization; openmetadata.health is read-only.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-openmetadata"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["openmetadata.sync"])
READ_COMMANDS: frozenset[str] = frozenset(["openmetadata.health"])
COMMAND_RESOURCE_MAP: dict[str, str] = {"openmetadata.sync": "metadata_catalog"}
COMMAND_ACTION_MAP: dict[str, str] = {"openmetadata.sync": "metadata.sync"}

OpenMetadataSurfaceAdapter = cli_surface_adapter_class(
    "OpenMetadataSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="openmetadata",
    default_resource_prefix="openmetadata",
)


def get_openmetadata_adapter() -> CliSurfaceAdapter:
    """Return the process-wide OpenMetadata CLI surface adapter singleton."""
    return OpenMetadataSurfaceAdapter.get_instance()
