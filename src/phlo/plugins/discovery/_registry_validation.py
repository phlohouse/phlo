"""Plugin interface validation helpers for registry operations.

Checks each plugin against the required methods of its concrete base class;
unknown plugin types pass unvalidated rather than rejected. Failures return
False and log at debug level instead of raising, so one bad plugin never
breaks discovery.
Private helper of phlo.plugins.discovery, imported only by registry.py during
plugin registration; validates plugins against phlo.plugins.base contracts.
"""

from __future__ import annotations

from typing import Any

from phlo.plugins.base import (
    AssetProviderPlugin,
    CatalogPlugin,
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


def validate_plugin_interface(plugin: Plugin, logger: Any) -> bool:
    """Validate plugin interface compliance."""
    if not hasattr(plugin, "metadata"):
        return False

    try:
        metadata = plugin.metadata
        if not all(hasattr(metadata, field) for field in ("name", "version")):
            return False
    except Exception:
        logger.debug("plugin_validation_metadata_access_failed", exc_info=True)
        return False

    if isinstance(plugin, SourceConnectorPlugin):
        return hasattr(plugin, "fetch_data") and callable(plugin.fetch_data)
    if isinstance(plugin, QualityCheckPlugin):
        return hasattr(plugin, "create_check") and callable(plugin.create_check)
    if isinstance(plugin, QualityProviderPlugin):
        return hasattr(plugin, "get_decorator") and callable(plugin.get_decorator)
    if isinstance(plugin, IngestionProviderPlugin):
        return hasattr(plugin, "get_decorator") and callable(plugin.get_decorator)
    if isinstance(plugin, TransformationPlugin):
        return hasattr(plugin, "transform") and callable(plugin.transform)
    if isinstance(plugin, TransformationProviderPlugin):
        return hasattr(plugin, "get_asset_retriever") and callable(plugin.get_asset_retriever)
    if isinstance(plugin, ServicePlugin):
        try:
            service_definition = plugin.service_definition
        except Exception:
            logger.debug("plugin_validation_service_definition_failed", exc_info=True)
            return False
        return isinstance(service_definition, dict)
    if isinstance(plugin, HookPlugin):
        return hasattr(plugin, "get_hooks") and callable(plugin.get_hooks)
    if isinstance(plugin, AssetProviderPlugin):
        return hasattr(plugin, "get_assets") and callable(plugin.get_assets)
    if isinstance(plugin, ResourceProviderPlugin):
        return hasattr(plugin, "get_resources") and callable(plugin.get_resources)
    if isinstance(plugin, OrchestratorAdapterPlugin):
        return hasattr(plugin, "build_definitions") and callable(plugin.build_definitions)
    if isinstance(plugin, CatalogPlugin):
        has_catalog = hasattr(plugin, "catalog_name")
        has_targets = hasattr(plugin, "targets")
        has_properties = hasattr(plugin, "get_properties") and callable(plugin.get_properties)
        return has_catalog and has_targets and has_properties

    return True
