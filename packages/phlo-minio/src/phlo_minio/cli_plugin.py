"""MinIO CLI plugin registration.

Declares the plugin-neutral CLI command plugin that exposes the minio
command group to the Phlo CLI.

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class

from phlo_minio.cli import minio_group


MinioCliPlugin = cli_command_plugin_class(
    "MinioCliPlugin",
    name="minio",
    version="0.1.0",
    description="CLI commands for MinIO object store access",
    commands=[minio_group],
)
