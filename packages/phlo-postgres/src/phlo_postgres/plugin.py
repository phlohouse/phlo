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

from phlo.capabilities import PublishTargetSpec, ResourceSpec, SettingsStoreSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin, service_plugin_class

from phlo_postgres.publish_target import PostgresPublishTarget
from phlo_postgres.resource import PostgresResource
from phlo_postgres.settings_store import get_settings_stores


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


PostgresVolumeSetupServicePlugin = service_plugin_class(
    "PostgresVolumeSetupServicePlugin",
    name="postgres-volume-setup",
    version="0.1.0",
    description="Initialize PostgreSQL data volume permissions",
    author="Phlo Team",
    tags=["core", "database", "postgres"],
    service_definition_file="volume_setup.yaml",
)


class PostgresResourceProvider(ResourceProviderPlugin):
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

    def get_resources(self) -> list[ResourceSpec]:
        """Return resource specifications exposed by this provider.

        Example:
            >>> provider = PostgresResourceProvider()
            >>> specs = provider.get_resources()
            >>> print(specs[0].name)
            postgres

        """
        return [ResourceSpec(name="postgres", resource=PostgresResource())]

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
