"""ClickHouse service and resource provider plugins.

This module provides Phlo plugin implementations for ClickHouse integration,
including service management, resource provisioning, and capability discovery.

Example:
    Using the ClickHouse plugins:

    >>> from phlo_clickhouse.plugin import ClickHouseServicePlugin
    >>> plugin = ClickHouseServicePlugin()
    >>> plugin.metadata.name
    'clickhouse'

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Publishes ClickHouse capability specs through phlo.capabilities and phlo.plugins.
"""

from __future__ import annotations

from phlo.capabilities import (
    CapabilitySupport,
    PublishTargetSpec,
    ResourceSpec,
    SlingConnectionSpec,
    TableStoreSpec,
)
from phlo.capabilities.specs import QueryEngineSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin, service_plugin_class
from phlo_clickhouse.publish_target import ClickHousePublishTarget
from phlo_clickhouse.resource import CLICKHOUSE_QUERY_ENGINE_SUPPORT, ClickHouseResource
from phlo_clickhouse.settings import get_settings as get_clickhouse_settings


ClickHouseServicePlugin = service_plugin_class(
    "ClickHouseServicePlugin",
    name="clickhouse",
    version="0.1.0",
    description="ClickHouse analytical database for data plane",
    author="Phlo Team",
    tags=["data", "query", "storage"],
)


ClickHouseSetupServicePlugin = service_plugin_class(
    "ClickHouseSetupServicePlugin",
    name="clickhouse-setup",
    version="0.1.0",
    description="Initialize ClickHouse databases for data plane",
    author="Phlo Team",
    tags=["data", "bootstrap"],
    service_definition_file="clickhouse-setup.yaml",
)


class ClickHouseResourceProvider(ResourceProviderPlugin):
    def get_sling_connections(self) -> list[SlingConnectionSpec]:
        """Expose the clickhouse Sling connection through the neutral seam."""
        from phlo_clickhouse.settings import get_settings

        return [SlingConnectionSpec(name="clickhouse", provider=get_settings())]

    """Resource provider plugin for ClickHouse.

    Provides ClickHouse resources, table stores, query engines, and
    publish targets to the Phlo capability framework.

    Example:
        >>> provider = ClickHouseResourceProvider()
        >>> resources = provider.get_resources()
        >>> len(resources)
        1

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for resource provider registration."""
        return PluginMetadata(
            name="clickhouse",
            version="0.1.0",
            description="ClickHouse resource for Phlo",
            support=CapabilitySupport(),
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Return list of ClickHouse resource specifications."""
        return [ResourceSpec(name="clickhouse", resource=ClickHouseResource())]

    def get_table_stores(self) -> list[TableStoreSpec]:
        """Return list of ClickHouse table store specifications."""
        return [
            TableStoreSpec(
                name="clickhouse",
                provider=ClickHouseResource(),
                support=CapabilitySupport(
                    supports_snapshots=False,
                    supports_schema_evolution=True,
                ),
            )
        ]

    def get_query_engines(self) -> list[QueryEngineSpec]:
        """Return list of ClickHouse query engine specifications.

        Reads current settings to populate connection metadata including
        host, port, and database information.
        """
        settings = get_clickhouse_settings()
        return [
            QueryEngineSpec(
                name="clickhouse",
                provider=ClickHouseResource(),
                metadata={
                    "host": settings.clickhouse_host,
                    "port": settings.clickhouse_http_port,
                    "native_port": settings.clickhouse_native_port,
                    "default_database": settings.clickhouse_db,
                    "service_type": "ClickHouse",
                },
                support=CLICKHOUSE_QUERY_ENGINE_SUPPORT,
            )
        ]

    def get_publish_targets(self) -> list[PublishTargetSpec]:
        """Return list of ClickHouse publish target specifications."""
        return [
            PublishTargetSpec(
                name="clickhouse",
                provider=ClickHousePublishTarget(),
                metadata={"target_system": "clickhouse", "role": "serving"},
            )
        ]
