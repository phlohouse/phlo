"""Shared constants for plugin discovery and registration.

Defines the canonical PLUGIN_FAMILIES table (entry-point group and plugin type
per family) plus the PHLO_NO_AUTO_DISCOVER env flag with its truthy/falsy
value sets. Private to the discovery package; import from the package root.
Private to phlo.plugins.discovery: shared by its auto-discovery, loading, query,
and registry modules; maps families onto phlo.plugins.base and hook types.
"""

from __future__ import annotations

from dataclasses import dataclass

from phlo.plugins.base import (
    AssetProviderPlugin,
    CatalogPlugin,
    CliCommandPlugin,
    IngestionProviderPlugin,
    OrchestratorAdapterPlugin,
    Plugin,
    QualityCheckPlugin,
    QualityProviderPlugin,
    ResourceProviderPlugin,
    ServicePlugin,
    SourceConnectorPlugin,
    TransformationPlugin,
    TransformationProviderPlugin,
)
from phlo.plugins.hooks import HookPlugin

NO_AUTO_DISCOVER_ENV = "PHLO_NO_AUTO_DISCOVER"
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off", ""})


@dataclass(frozen=True, slots=True)
class PluginFamilyDefinition:
    """Metadata for one canonical plugin family."""

    name: str
    key_prefix: str
    label: str
    entry_point_group: str
    plugin_type: type[Plugin]


PLUGIN_FAMILIES: dict[str, PluginFamilyDefinition] = {
    "source_connector": PluginFamilyDefinition(
        "source_connector",
        "source",
        "Source connector",
        "phlo.plugins.sources",
        SourceConnectorPlugin,
    ),
    "quality_check": PluginFamilyDefinition(
        "quality_check", "quality", "Quality check", "phlo.plugins.quality", QualityCheckPlugin
    ),
    "quality_provider": PluginFamilyDefinition(
        "quality_provider",
        "quality_provider",
        "Quality provider",
        "phlo.plugins.quality_providers",
        QualityProviderPlugin,
    ),
    "ingestion_provider": PluginFamilyDefinition(
        "ingestion_provider",
        "ingestion_provider",
        "Ingestion provider",
        "phlo.plugins.ingestion_providers",
        IngestionProviderPlugin,
    ),
    "transformation_provider": PluginFamilyDefinition(
        "transformation_provider",
        "transformation_provider",
        "Transformation provider",
        "phlo.plugins.transformation_providers",
        TransformationProviderPlugin,
    ),
    "transformation": PluginFamilyDefinition(
        "transformation",
        "transformation",
        "Transformation",
        "phlo.plugins.transforms",
        TransformationPlugin,
    ),
    "service": PluginFamilyDefinition(
        "service", "service", "Service", "phlo.plugins.services", ServicePlugin
    ),
    "cli_command": PluginFamilyDefinition(
        "cli_command", "cli", "CLI command", "phlo.plugins.cli", CliCommandPlugin
    ),
    "hook": PluginFamilyDefinition("hook", "hook", "Hook", "phlo.plugins.hooks", HookPlugin),
    "catalog": PluginFamilyDefinition(
        "catalog", "catalog", "Catalog", "phlo.plugins.catalogs", CatalogPlugin
    ),
    "asset_provider": PluginFamilyDefinition(
        "asset_provider", "assets", "Asset provider", "phlo.plugins.assets", AssetProviderPlugin
    ),
    "resource_provider": PluginFamilyDefinition(
        "resource_provider",
        "resources",
        "Resource provider",
        "phlo.plugins.resources",
        ResourceProviderPlugin,
    ),
    "orchestrator": PluginFamilyDefinition(
        "orchestrator",
        "orchestrator",
        "Orchestrator",
        "phlo.plugins.orchestrators",
        OrchestratorAdapterPlugin,
    ),
}

ENTRY_POINT_GROUPS = {
    family: definition.entry_point_group for family, definition in PLUGIN_FAMILIES.items()
}
PLUGIN_EXPECTED_TYPES = {
    family: definition.plugin_type for family, definition in PLUGIN_FAMILIES.items()
}


def plugin_family(name: str) -> PluginFamilyDefinition:
    """Return metadata for a canonical plugin family."""
    try:
        return PLUGIN_FAMILIES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown plugin family: {name}") from exc
