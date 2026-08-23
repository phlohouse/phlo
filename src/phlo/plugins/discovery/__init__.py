"""
Plugin Discovery Module

Consolidates plugin and service discovery into a single module under phlo.plugins.

This module provides a unified interface for discovering:
- Plugins (via entry points)
- Services (from plugins and core)
- Local in-memory registry access

Remote registry package discovery lives in phlo.plugins.registry_client
(e.g., list_registry_plugins) and is not re-exported here.

Package facade for plugin discovery: re-exports the registry, loading, and query helpers under
one phlo.plugins.discovery namespace for the rest of the system.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from phlo.plugins.discovery.registry import (
    PluginRegistry,
    get_global_registry,
)
from phlo.plugins.discovery.services import (
    ServiceDefinition,
    ServiceDiscovery,
)

if TYPE_CHECKING:
    from phlo.plugins.discovery._plugin_constants import (
        ENTRY_POINT_GROUPS,
        PLUGIN_FAMILIES,
        PluginFamilyDefinition,
    )
    from phlo.plugins.discovery._plugin_loading import (
        discover_plugins,
    )
    from phlo.plugins.discovery._plugin_queries import (
        get_plugin,
        get_plugin_info,
        list_plugins,
        validate_plugins,
    )

_PLUGIN_EXPORTS = frozenset(
    {
        "ENTRY_POINT_GROUPS",
        "PLUGIN_FAMILIES",
        "PluginFamilyDefinition",
        "discover_plugins",
        "get_plugin",
        "get_plugin_info",
        "list_plugins",
        "validate_plugins",
    }
)


def __getattr__(name: str):
    """Lazily expose entry-point plugin discovery helpers.

    Importing service discovery or hook emitters must not eagerly import every
    installed plugin. Doing so can re-enter discovery while provider modules are
    still being initialized.
    """
    if name in {"ENTRY_POINT_GROUPS", "PLUGIN_FAMILIES", "PluginFamilyDefinition"}:
        constants_module = importlib.import_module("phlo.plugins.discovery._plugin_constants")
        return getattr(constants_module, name)
    if name == "discover_plugins":
        loading_module = importlib.import_module("phlo.plugins.discovery._plugin_loading")
        return getattr(loading_module, name)
    if name in {"get_plugin", "get_plugin_info", "list_plugins", "validate_plugins"}:
        queries_module = importlib.import_module("phlo.plugins.discovery._plugin_queries")
        return getattr(queries_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Plugin discovery
    "ENTRY_POINT_GROUPS",
    "PLUGIN_FAMILIES",
    "PluginFamilyDefinition",
    "discover_plugins",
    "get_plugin",
    "get_plugin_info",
    "list_plugins",
    "validate_plugins",
    # Registry
    "PluginRegistry",
    "get_global_registry",
    # Service discovery
    "ServiceDefinition",
    "ServiceDiscovery",
]
