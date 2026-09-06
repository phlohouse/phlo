"""PostgreSQL service plugin implementations.

This module provides plugin implementations that integrate PostgreSQL with the
phlo plugin system. It includes service plugins for the PostgreSQL database,
Prometheus exporter, and volume setup, as well as a resource provider plugin
that exposes PostgreSQL capabilities to the rest of the system.

Example:
    >>> from phlo_postgres.plugin import PostgresServicePlugin
    >>> plugin = PostgresServicePlugin()
    >>> print(plugin.metadata.name)
    postgres
    >>> definition = plugin.service_definition

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Publishes PostgreSQL resource and publish-target specs through phlo.capabilities.
"""

from __future__ import annotations

import shlex
from typing import Any

from phlo.capabilities import (
    PublishTargetSpec,
    ResourceSpec,
    SettingsStoreSpec,
    SlingConnectionSpec,
    BackendReadinessSpec,
    BackupContributorSpec,
    DatasetStateStoreSpec,
)
from phlo.plugins import (
    PackageYamlServicePlugin,
    PluginMetadata,
    ResourceProviderPlugin,
    service_plugin_class,
)

from phlo_postgres.dataset_state_store import get_dataset_state_stores
from phlo_postgres.publish_target import PostgresPublishTarget
from phlo_postgres.resource import PostgresResource
from phlo_postgres.settings_store import get_settings_stores


POSTGRES_DATA_DIR = "/var/lib/postgresql"

_PRE_18_VOLUME_MESSAGE = (
    "PostgreSQL 16 data volume detected. Back it up with PostgreSQL 16, "
    "then restore it into a new PostgreSQL 18 volume before starting Phlo."
)


def pre_18_volume_guard(data_dir: str = POSTGRES_DATA_DIR) -> str:
    """Return the shell conditional that refuses pre-18 PostgreSQL data volumes.

    Data directories written by PostgreSQL 16 or earlier carry a PG_VERSION
    file; starting Phlo against one would use an incompatible on-disk layout.
    The guard prints remediation guidance to stderr and exits 1 before the
    volume is touched.
    """
    pg_version_file = shlex.quote(f"{data_dir}/PG_VERSION")
    return f"if [ -f {pg_version_file} ]; then echo '{_PRE_18_VOLUME_MESSAGE}' >&2; exit 1; fi"


def volume_setup_command(data_dir: str = POSTGRES_DATA_DIR) -> str:
    """Return the /bin/sh -c payload that initializes the data volume.

    Runs the pre-18 guard first, then fixes ownership for the postgres user
    (uid/gid 70) so the server can use the volume.
    """
    quoted_dir = shlex.quote(data_dir)
    return (
        f'-c "{pre_18_volume_guard(data_dir)} && mkdir -p {quoted_dir}'
        f" && chown -R 70:70 {quoted_dir} && chmod 700 {quoted_dir}"
        " && echo 'Postgres data volume ownership initialized'\""
    )


class PostgresVolumeSetupServicePlugin(PackageYamlServicePlugin):
    """Initializes the PostgreSQL data volume before the server starts."""

    _service_definition_file = "volume_setup.yaml"

    @property
    def metadata(self) -> PluginMetadata:
        """Return the static metadata for the volume-setup service."""
        return PluginMetadata(
            name="postgres-volume-setup",
            version="0.1.0",
            description="Initialize PostgreSQL data volume permissions",
            author="Phlo Team",
            tags=["core", "database", "postgres"],
        )

    @property
    def service_definition(self) -> dict[str, Any]:
        """Return the packaged definition with the guard command injected."""
        definition = super().service_definition
        definition["compose"]["command"] = volume_setup_command()
        return definition


PostgresServicePlugin = service_plugin_class(
    "PostgresServicePlugin",
    name="postgres",
    version="0.1.0",
    description="PostgreSQL database for metadata and operational storage",
    author="Phlo Team",
    tags=["core", "database", "postgres"],
)


PostgresExporterServicePlugin = service_plugin_class(
    "PostgresExporterServicePlugin",
    name="postgres-exporter",
    version="0.1.0",
    description="Prometheus exporter for PostgreSQL metrics",
    author="Phlo Team",
    tags=["observability", "metrics", "postgres"],
    service_definition_file="exporter_service.yaml",
)


class PostgresResourceProvider(ResourceProviderPlugin):
    def get_backend_readiness(self) -> list[BackendReadinessSpec]:
        """Expose the postgres security readiness inspector (read-only)."""
        from phlo_postgres.security_readiness import PostgresReadinessProvider

        return [BackendReadinessSpec(name="postgres", provider=PostgresReadinessProvider())]

    def get_backup_contributors(self) -> list[BackupContributorSpec]:
        """Expose the postgres backup contribution capability (ADR 0049 §3)."""
        from phlo_postgres.continuity import PostgresBackupContributor

        return [BackupContributorSpec(name="postgres", provider=PostgresBackupContributor())]

    """Resource provider plugin that exposes PostgreSQL capabilities.

    This plugin registers the PostgresResource and PostgresPublishTarget with
    the phlo resource system, making them available to other components for
    database operations and data publishing.

    Example:
        >>> provider = PostgresResourceProvider()
        >>> resources = provider.get_resources()
        >>> targets = provider.get_publish_targets()

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the PostgreSQL resource provider.

        Example:
            >>> provider = PostgresResourceProvider()
            >>> meta = provider.metadata
            >>> print(meta.name)
            postgres

        """
        return PluginMetadata(
            name="postgres",
            version="0.1.0",
            description="Postgres resource for Phlo",
        )

    def get_sling_connections(self) -> list[SlingConnectionSpec]:
        """Expose the PostgreSQL Sling connection through the neutral seam."""
        from phlo_postgres.settings import get_settings

        return [SlingConnectionSpec(name="postgres", provider=get_settings())]

    def get_resources(self) -> list[ResourceSpec]:
        """Return resource specifications exposed by this provider.

        Example:
            >>> provider = PostgresResourceProvider()
            >>> specs = provider.get_resources()
            >>> print(specs[0].name)
            postgres

        """
        from phlo_postgres.checkpoints import PostgresIngestionCheckpointStore

        return [
            ResourceSpec(name="postgres", resource=PostgresResource()),
            ResourceSpec(
                name="ingestion_checkpoints",
                resource=PostgresIngestionCheckpointStore(),
            ),
        ]

    def get_publish_targets(self) -> list[PublishTargetSpec]:
        """Return publish target capability specs exposed by this provider.

        Example:
            >>> provider = PostgresResourceProvider()
            >>> targets = provider.get_publish_targets()
            >>> print(targets[0].name)
            postgres
            >>> print(targets[0].metadata)
            {'target_system': 'postgres', 'role': 'serving'}

        """
        return [
            PublishTargetSpec(
                name="postgres",
                provider=PostgresPublishTarget(),
                metadata={"target_system": "postgres", "role": "serving"},
            )
        ]

    def get_settings_stores(self) -> list[SettingsStoreSpec]:
        """Return settings store capability specs for durable Observatory settings.

        Wraps a
        :class:`~phlo_postgres.settings_store.PostgresSettingsStore`
        that persists Observatory settings to PostgreSQL.
        """
        return get_settings_stores()

    def get_dataset_state_stores(self) -> list[DatasetStateStoreSpec]:
        """Return dataset state store capability specs.

        Wraps the provider-owned durable Dataset workflow store built on the
        transactional settings service; registered through the
        ``dataset_state_store`` capability family so core never imports this
        package.
        """
        return get_dataset_state_stores()
