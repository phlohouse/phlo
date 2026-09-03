"""Capability registry for named capability families.

Defines the canonical capability families and a thread-safe process-global
registry keyed by family name. Resource providers register specs per family;
clearing is per-family or global, which keeps tests isolated when discovery
runs repeatedly in one process.

Central capability registry: imported by phlo-dagster adapters, phlo.rbac, and
phlo-api consumers.
"""

from __future__ import annotations

import builtins
import threading
from dataclasses import dataclass, field
from typing import Any

from phlo.capabilities.catalog import CapabilityFamily, CapabilityFamilyDefinition
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
    NamespaceResolverSpec,
    ObjectStoreSpec,
    ObservabilityBackendSpec,
    OrchestratorOperationsSpec,
    PublishTargetSpec,
    QualityBackendSpec,
    QueryEngineSpec,
    RegulatedSurfaceSpec,
    ResourceSpec,
    SchemaDiscoverySpec,
    SchemaMigrationSpec,
    SecretBackendSpec,
    SettingsStoreSpec,
    SlingConnectionSpec,
    TableStoreSpec,
    UiContributionSpec,
    WorkflowAuthoringSpec,
    WorkflowValidationSpec,
)

CAPABILITY_FAMILIES: dict[str, CapabilityFamilyDefinition[Any, Any]] = {
    "asset": CapabilityFamilyDefinition(
        name="asset",
        spec_type=AssetSpec,
        key=lambda spec: spec.key,
        provider_method="get_assets",
    ),
    "check": CapabilityFamilyDefinition(
        name="check",
        spec_type=AssetCheckSpec,
        key=lambda spec: (spec.asset_key, spec.name),
        provider_method="get_checks",
    ),
    "resource": CapabilityFamilyDefinition(
        name="resource",
        spec_type=ResourceSpec,
        key=lambda spec: spec.name,
        provider_method="get_resources",
    ),
    "table_store": CapabilityFamilyDefinition(
        name="table_store",
        spec_type=TableStoreSpec,
        key=lambda spec: spec.name,
        provider_method="get_table_stores",
    ),
    "catalog": CapabilityFamilyDefinition(
        name="catalog",
        spec_type=CatalogSpec,
        key=lambda spec: spec.name,
        provider_method="get_catalogs",
    ),
    "catalog_scanner": CapabilityFamilyDefinition(
        name="catalog_scanner",
        spec_type=CatalogScannerSpec,
        key=lambda spec: spec.name,
        provider_method="get_catalog_scanners",
    ),
    "query_engine": CapabilityFamilyDefinition(
        name="query_engine",
        spec_type=QueryEngineSpec,
        key=lambda spec: spec.name,
        provider_method="get_query_engines",
    ),
    "maintenance_executor": CapabilityFamilyDefinition(
        name="maintenance_executor",
        spec_type=MaintenanceExecutorSpec,
        key=lambda spec: spec.name,
        provider_method="get_maintenance_executors",
    ),
    "object_store": CapabilityFamilyDefinition(
        name="object_store",
        spec_type=ObjectStoreSpec,
        key=lambda spec: spec.name,
        provider_method="get_object_stores",
    ),
    "sling_connection": CapabilityFamilyDefinition(
        name="sling_connection",
        spec_type=SlingConnectionSpec,
        key=lambda spec: spec.name,
        provider_method="get_sling_connections",
    ),
    "quality_backend": CapabilityFamilyDefinition(
        name="quality_backend",
        spec_type=QualityBackendSpec,
        key=lambda spec: spec.name,
        provider_method="get_quality_backends",
    ),
    "maintenance_read_model": CapabilityFamilyDefinition(
        name="maintenance_read_model",
        spec_type=MaintenanceReadModelSpec,
        key=lambda spec: spec.name,
        provider_method="get_maintenance_read_models",
    ),
    "metadata_catalog": CapabilityFamilyDefinition(
        name="metadata_catalog",
        spec_type=MetadataCatalogSpec,
        key=lambda spec: spec.name,
        provider_method="get_metadata_catalogs",
    ),
    "lineage_sink": CapabilityFamilyDefinition(
        name="lineage_sink",
        spec_type=LineageSinkSpec,
        key=lambda spec: spec.name,
        provider_method="get_lineage_sinks",
    ),
    "governance_backend": CapabilityFamilyDefinition(
        name="governance_backend",
        spec_type=GovernanceBackendSpec,
        key=lambda spec: spec.name,
        provider_method="get_governance_backends",
    ),
    "authorization_policy_backend": CapabilityFamilyDefinition(
        name="authorization_policy_backend",
        spec_type=AuthorizationPolicyBackendSpec,
        key=lambda spec: spec.name,
        provider_method="get_authorization_policy_backends",
    ),
    "authentication_provider": CapabilityFamilyDefinition(
        name="authentication_provider",
        spec_type=AuthenticationProviderSpec,
        key=lambda spec: spec.name,
        provider_method="get_authentication_providers",
    ),
    "publish_target": CapabilityFamilyDefinition(
        name="publish_target",
        spec_type=PublishTargetSpec,
        key=lambda spec: spec.name,
        provider_method="get_publish_targets",
    ),
    "alert_sink": CapabilityFamilyDefinition(
        name="alert_sink",
        spec_type=AlertSinkSpec,
        key=lambda spec: spec.name,
        provider_method="get_alert_sinks",
    ),
    "api_backend": CapabilityFamilyDefinition(
        name="api_backend",
        spec_type=ApiBackendSpec,
        key=lambda spec: spec.name,
        provider_method="get_api_backends",
    ),
    "secret_backend": CapabilityFamilyDefinition(
        name="secret_backend",
        spec_type=SecretBackendSpec,
        key=lambda spec: spec.name,
        provider_method="get_secret_backends",
    ),
    "schema_migrator": CapabilityFamilyDefinition(
        name="schema_migrator",
        spec_type=SchemaMigrationSpec,
        key=lambda spec: spec.name,
        provider_method="get_schema_migrators",
    ),
    "workflow_validation": CapabilityFamilyDefinition(
        name="workflow_validation",
        spec_type=WorkflowValidationSpec,
        key=lambda spec: spec.name,
        provider_method="get_workflow_validators",
    ),
    "schema_discovery": CapabilityFamilyDefinition(
        name="schema_discovery",
        spec_type=SchemaDiscoverySpec,
        key=lambda spec: spec.name,
        provider_method="get_schema_discovery_providers",
    ),
    "namespace_resolver": CapabilityFamilyDefinition(
        name="namespace_resolver",
        spec_type=NamespaceResolverSpec,
        key=lambda spec: spec.name,
        provider_method="get_namespace_resolvers",
    ),
    "workflow_authoring": CapabilityFamilyDefinition(
        name="workflow_authoring",
        spec_type=WorkflowAuthoringSpec,
        key=lambda spec: spec.name,
        provider_method="get_workflow_authoring_providers",
    ),
    "orchestrator_operations": CapabilityFamilyDefinition(
        name="orchestrator_operations",
        spec_type=OrchestratorOperationsSpec,
        key=lambda spec: spec.name,
        provider_method="get_orchestrator_operations_providers",
    ),
    "data_migration_source": CapabilityFamilyDefinition(
        name="data_migration_source",
        spec_type=DataMigrationSourceSpec,
        key=lambda spec: spec.name,
        provider_method="get_data_migration_sources",
    ),
    "observability_backend": CapabilityFamilyDefinition(
        name="observability_backend",
        spec_type=ObservabilityBackendSpec,
        key=lambda spec: spec.name,
        provider_method="get_observability_backends",
    ),
    "regulated_surface": CapabilityFamilyDefinition(
        name="regulated_surface",
        spec_type=RegulatedSurfaceSpec,
        key=lambda spec: spec.name,
    ),
    "ui_contribution": CapabilityFamilyDefinition(
        name="ui_contribution",
        spec_type=UiContributionSpec,
        key=lambda spec: spec.name,
    ),
    "settings_store": CapabilityFamilyDefinition(
        name="settings_store",
        spec_type=SettingsStoreSpec,
        key=lambda spec: spec.name,
        provider_method="get_settings_stores",
    ),
}


def capability_family(name: str) -> CapabilityFamilyDefinition[Any, Any]:
    """Return metadata for a canonical capability family."""
    try:
        return CAPABILITY_FAMILIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown capability family: {name}") from exc


def iter_provider_capabilities(provider: Any) -> list[tuple[str, list[Any]]]:
    """Return all capability specs exposed by a resource provider."""
    discovered: list[tuple[str, list[Any]]] = []
    for family_name, definition in CAPABILITY_FAMILIES.items():
        if definition.provider_method is None or not hasattr(provider, definition.provider_method):
            continue
        specs = definition.provider_specs(provider)
        if specs:
            discovered.append((family_name, specs))
    return discovered


@dataclass
class CapabilityRegistry:
    """Thread-safe in-memory registry for capability specifications."""

    _families: dict[str, CapabilityFamily[Any, Any]] = field(init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._families = {
            name: definition.family() for name, definition in CAPABILITY_FAMILIES.items()
        }

    def register(self, family: str, spec: Any) -> None:
        """Register or replace a capability spec in a canonical family."""
        definition = capability_family(family)
        if not isinstance(spec, definition.spec_type):
            raise TypeError(
                f"Capability family '{family}' expects {definition.spec_type.__name__}, "
                f"got {type(spec).__name__}."
            )
        with self._lock:
            self._families[family].register(spec)

    def list(self, family: str) -> builtins.list[Any]:
        """Return a snapshot list of registered specs for one family."""
        capability_family(family)
        with self._lock:
            return self._families[family].list()

    def clear(self, family: str) -> None:
        """Remove all registered specs for one capability family."""
        capability_family(family)
        with self._lock:
            self._families[family].clear()

    def clear_all(self) -> None:
        """Remove all specs from every capability family."""
        with self._lock:
            for family_name in CAPABILITY_FAMILIES:
                self._families[family_name].clear()


_GLOBAL_REGISTRY = CapabilityRegistry()


def get_capability_registry() -> CapabilityRegistry:
    """Return the process-global capability registry instance."""
    return _GLOBAL_REGISTRY


def register_capability(family: str, spec: Any) -> None:
    """Register a capability spec in the process-global registry."""
    _GLOBAL_REGISTRY.register(family, spec)


def clear_capabilities(family: str) -> None:
    """Clear one capability family from the process-global registry."""
    _GLOBAL_REGISTRY.clear(family)


def clear_all_capabilities() -> None:
    """Clear every capability family from the process-global registry."""
    _GLOBAL_REGISTRY.clear_all()
