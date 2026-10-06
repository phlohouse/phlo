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

_REQUIRED_METHODS: tuple[tuple[type[Plugin], str], ...] = (
    (SourceConnectorPlugin, "fetch_data"),
    (QualityCheckPlugin, "create_check"),
    (QualityProviderPlugin, "get_decorator"),
    (IngestionProviderPlugin, "get_decorator"),
    (TransformationPlugin, "transform"),
    (TransformationProviderPlugin, "get_asset_retriever"),
)
_OTHER_REQUIRED_METHODS: tuple[tuple[type[Plugin], str], ...] = (
    (HookPlugin, "get_hooks"),
    (AssetProviderPlugin, "get_assets"),
    (ResourceProviderPlugin, "get_resources"),
    (OrchestratorAdapterPlugin, "build_definitions"),
)


def _has_callable(plugin: Plugin, attribute: str) -> bool:
    return hasattr(plugin, attribute) and callable(getattr(plugin, attribute))


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

    for plugin_type, method in _REQUIRED_METHODS:
        if isinstance(plugin, plugin_type):
            return _has_callable(plugin, method)
    if isinstance(plugin, ServicePlugin):
        try:
            service_definition = plugin.service_definition
        except Exception:
            logger.debug("plugin_validation_service_definition_failed", exc_info=True)
            return False
        return isinstance(service_definition, dict)
    for plugin_type, method in _OTHER_REQUIRED_METHODS:
        if isinstance(plugin, plugin_type):
            return _has_callable(plugin, method)
    if isinstance(plugin, CatalogPlugin):
        has_catalog = hasattr(plugin, "catalog_name")
        has_targets = hasattr(plugin, "targets")
        has_properties = _has_callable(plugin, "get_properties")
        return has_catalog and has_targets and has_properties

    return True
