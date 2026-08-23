"""Provider plugin classes.

This module defines plugin types that provide asset and resource specifications.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from phlo.capabilities.specs import (
    AlertSinkSpec,
    ApiBackendSpec,
    AssetCheckSpec,
    AssetSpec,
    AuthenticationProviderSpec,
    AuthorizationPolicyBackendSpec,
    CatalogScannerSpec,
    CatalogSpec,
    DataMigrationSourceSpec,
    GovernanceBackendSpec,
    LineageSinkSpec,
    MaintenanceExecutorSpec,
    MaintenanceReadModelSpec,
    MetadataCatalogSpec,
    ObjectStoreSpec,
    ObservabilityBackendSpec,
    PublishTargetSpec,
    QualityBackendSpec,
    QueryEngineSpec,
    ResourceSpec,
    SchemaMigrationSpec,
    SecretBackendSpec,
    TableStoreSpec,
)
from phlo.plugins.base.plugin import Plugin


class AssetProviderPlugin(Plugin, ABC):
    """Base class for capability plugins that provide asset specs."""

    @property
    def requires_capabilities(self) -> list[str]:
        """Return required capabilities for this provider."""
        return list(self.metadata.requires_capabilities)

    @property
    def optional_capabilities(self) -> list[str]:
        """Return optional capabilities for this provider."""
        return list(self.metadata.optional_capabilities)

    @abstractmethod
    def get_assets(self) -> Iterable[AssetSpec]:
        """Return asset specifications exposed by this plugin."""
        raise NotImplementedError

    def get_checks(self) -> Iterable[AssetCheckSpec]:
        """Return asset check specifications exposed by this plugin."""
        return []


class ResourceProviderPlugin(Plugin, ABC):
    """Base class for plugins that provide resource specs."""

    @property
    def requires_capabilities(self) -> list[str]:
        """Return required capabilities for this provider."""
        return list(self.metadata.requires_capabilities)

    @property
    def optional_capabilities(self) -> list[str]:
        """Return optional capabilities for this provider."""
        return list(self.metadata.optional_capabilities)

    @abstractmethod
    def get_resources(self) -> Iterable[ResourceSpec]:
        """Return resource specifications exposed by this plugin."""
        raise NotImplementedError

    def get_table_stores(self) -> Iterable[TableStoreSpec]:
        """Return table store capability specs exposed by this plugin."""
        return []

    def get_catalogs(self) -> Iterable[CatalogSpec]:
        """Return catalog capability specs exposed by this plugin."""
        return []

    def get_catalog_scanners(self) -> Iterable[CatalogScannerSpec]:
        """Return catalog scanner capability specs exposed by this plugin."""
        return []

    def get_query_engines(self) -> Iterable[QueryEngineSpec]:
        """Return query engine capability specs exposed by this plugin."""
        return []

    def get_maintenance_executors(self) -> Iterable[MaintenanceExecutorSpec]:
        """Return ref-aware maintenance executor specs exposed by this plugin."""
        return []

    def get_object_stores(self) -> Iterable[ObjectStoreSpec]:
        """Return object-store capability specs exposed by this plugin."""
        return []

    def get_quality_backends(self) -> Iterable[QualityBackendSpec]:
        """Return quality backend capability specs exposed by this plugin."""
        return []

    def get_maintenance_read_models(self) -> Iterable[MaintenanceReadModelSpec]:
        """Return maintenance read-model capability specs exposed by this plugin."""
        return []

    def get_metadata_catalogs(self) -> Iterable[MetadataCatalogSpec]:
        """Return metadata catalog capability specs exposed by this plugin."""
        return []

    def get_lineage_sinks(self) -> Iterable[LineageSinkSpec]:
        """Return lineage sink capability specs exposed by this plugin."""
        return []

    def get_governance_backends(self) -> Iterable[GovernanceBackendSpec]:
        """Return governance backend capability specs exposed by this plugin."""
        return []

    def get_authorization_policy_backends(self) -> Iterable[AuthorizationPolicyBackendSpec]:
        """Return authorization policy backend capability specs exposed by this plugin."""
        return []

    def get_authentication_providers(self) -> Iterable[AuthenticationProviderSpec]:
        """Return authentication provider capability specs exposed by this plugin."""
        return []

    def get_publish_targets(self) -> Iterable[PublishTargetSpec]:
        """Return publish target capability specs exposed by this plugin."""
        return []

    def get_alert_sinks(self) -> Iterable[AlertSinkSpec]:
        """Return alert sink capability specs exposed by this plugin."""
        return []

    def get_api_backends(self) -> Iterable[ApiBackendSpec]:
        """Return API backend capability specs exposed by this plugin."""
        return []

    def get_secret_backends(self) -> Iterable[SecretBackendSpec]:
        """Return secret backend capability specs exposed by this plugin."""
        return []

    def get_schema_migrators(self) -> Iterable[SchemaMigrationSpec]:
        """Return schema migrator capability specs exposed by this plugin."""
        return []

    def get_data_migration_sources(self) -> Iterable[DataMigrationSourceSpec]:
        """Return data migration source adapter specs exposed by this plugin."""
        return []

    def get_observability_backends(self) -> Iterable[ObservabilityBackendSpec]:
        """Return observability backend capability specs exposed by this plugin."""
        return []
