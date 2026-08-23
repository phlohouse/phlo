"""phlo_minio CLI authorization table.

Declares which phlo-minio commands are reads versus mutations and maps each
to its storage resource/action pair; builds a singleton surface adapter
from those tables for the shared policy layer.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-minio-cli"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(["minio"])
READ_COMMANDS: frozenset[str] = frozenset(["minio.admin.info", "minio.ls"])
COMMAND_RESOURCE_MAP: dict[str, str] = {
    "minio": "storage",
    "minio.ls": "storage",
    "minio.admin.info": "storage",
}
COMMAND_ACTION_MAP: dict[str, str] = {
    "minio": "storage.manage",
    "minio.ls": "storage.read",
    "minio.admin.info": "storage.read",
}

MinioCliSurfaceAdapter = cli_surface_adapter_class(
    "MinioCliSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="minio",
    default_resource_prefix="minio",
)


def get_minio_cli_adapter() -> CliSurfaceAdapter:
    """Return the shared MinIO CLI surface adapter instance."""
    return MinioCliSurfaceAdapter.get_instance()
