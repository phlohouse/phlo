"""Service and resource provider plugins for Trino.

This module implements the plugin interfaces for Trino integration with Phlo.
It provides service orchestration, resource provisioning, and capability
registration for the Trino query engine.

Classes:
    TrinoServicePlugin: Service plugin for Trino container orchestration.
    TrinoResourceProvider: Resource provider for Trino query capabilities.

The plugins register Trino as:
    - A query engine with time-travel and ref support
    - A governance backend for SQL-based access control
    - A service managed via Docker Compose

Example:
    Plugins are automatically discovered via entry points:
    >>> from phlo.plugins import discover_plugins
    >>> plugins = discover_plugins()


    Trino plugin module; its resource and service plugins register via phlo plugin entry points.
    Builds on phlo.capabilities.specs, the phlo.plugins interfaces, and phlo_trino internals.
"""

from __future__ import annotations

from phlo.capabilities import BackendReadinessSpec, CapabilitySupport, ResourceSpec
from phlo.capabilities.specs import (
    GovernanceBackendSpec,
    MaintenanceExecutorSpec,
    QueryEngineSpec,
)
from phlo.plugins import PluginMetadata, ResourceProviderPlugin, service_plugin_class
from phlo_trino.governance import TrinoGovernanceBackend
from phlo_trino.resource import TRINO_QUERY_ENGINE_SUPPORT, TrinoResource
from phlo_trino.settings import get_settings as get_trino_settings

TRINO_COMPATIBILITY_METADATA = {
    "target": "apache-iceberg-1.11",
    "rest_catalog": {"trino_ref_strategy": "rest-catalog-prefix"},
    "engines": {
        "trino": {
            "catalog_type": "rest",
            "iceberg_table_spec_versions": [1, 2],
        }
    },
    "checks": ["trino-prefix-property", "trino-table-spec-v1-v2"],
}


TrinoServicePlugin = service_plugin_class(
    "TrinoServicePlugin",
    name="trino",
    version="0.1.0",
    description="Distributed SQL query engine",
    author="Phlo Team",
    tags=["core", "query"],
)


class TrinoResourceProvider(ResourceProviderPlugin):
    def get_backend_readiness(self) -> list[BackendReadinessSpec]:
        """Expose the trino security readiness inspector (read-only)."""
        from phlo_trino.security_readiness import TrinoReadinessProvider

        return [BackendReadinessSpec(name="trino", provider=TrinoReadinessProvider())]

    """Resource provider plugin for Trino."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata used by plugin discovery."""
        return PluginMetadata(
            name="trino",
            version="0.1.0",
            description="Trino resource for Phlo",
            support=TRINO_QUERY_ENGINE_SUPPORT,
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Return the Trino resource specification."""
        return [ResourceSpec(name="trino", resource=TrinoResource())]

    def get_query_engines(self) -> list[QueryEngineSpec]:
        """Return the Trino query-engine capability specification."""
        return [
            QueryEngineSpec(
                name="trino",
                provider=TrinoResource(),
                metadata={
                    "host": get_trino_settings().trino_host,
                    "port": get_trino_settings().trino_port,
                    "default_catalog": get_trino_settings().trino_catalog,
                    "default_ref": get_trino_settings().trino_default_ref,
                    "service_type": "Trino",
                    "sqlalchemy_uri_template": "trino://{host}:{port}/{default_catalog}",
                    "compatibility": TRINO_COMPATIBILITY_METADATA,
                },
                support=TRINO_QUERY_ENGINE_SUPPORT,
            )
        ]

    def get_maintenance_executors(self) -> list[MaintenanceExecutorSpec]:
        """Return the ref-aware Trino maintenance executor."""
        return [
            MaintenanceExecutorSpec(
                name="trino",
                provider=TrinoResource(),
                metadata={
                    "service_type": "Trino",
                    "default_catalog": get_trino_settings().trino_catalog,
                    "default_ref": get_trino_settings().trino_default_ref,
                },
                support=TRINO_QUERY_ENGINE_SUPPORT,
            )
        ]

    def get_governance_backends(self) -> list[GovernanceBackendSpec]:
        """Return the Trino governance backend specification for SQL grants."""
        return [
            GovernanceBackendSpec(
                name="trino",
                provider=TrinoGovernanceBackend(),
                support=CapabilitySupport(),
            )
        ]
