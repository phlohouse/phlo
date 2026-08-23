"""Query helpers over the global plugin registry.

Read-only wrappers around list/get/info/validation; none of these trigger
discovery, so callers populate the registry first.

Private discovery helper imported by the phlo.plugins.discovery package init; wraps the plugin
registry with read-only list/get/info/validation queries built on phlo.plugins.base.
"""

from __future__ import annotations

from phlo.logging import get_logger
from phlo.plugins.base import Plugin
from phlo.plugins.discovery._plugin_constants import plugin_family
from phlo.plugins.discovery.registry import get_global_registry

logger = get_logger(__name__)


def list_plugins(plugin_type: str | None = None) -> dict[str, list[str]]:
    """List all plugins in the global registry."""
    registry = get_global_registry()
    all_plugins = registry.list_all_plugins()

    if plugin_type:
        plugin_family(plugin_type)
        return {plugin_type: all_plugins[plugin_type]}

    return all_plugins


def get_plugin(plugin_type: str, name: str) -> Plugin | None:
    """Get a plugin by type and name."""
    registry = get_global_registry()
    return registry.get(plugin_type, name)


def get_plugin_info(plugin_type: str, name: str) -> dict | None:
    """Get detailed metadata for a plugin."""
    registry = get_global_registry()
    return registry.get_plugin_metadata(plugin_type, name)


def validate_plugins() -> dict[str, list[str]]:
    """Validate all registered plugins."""
    registry = get_global_registry()
    all_plugins = registry.list_all_plugins()

    valid: list[str] = []
    invalid: list[str] = []

    for current_type, plugin_names in all_plugins.items():
        for plugin_name in plugin_names:
            plugin = registry.get(current_type, plugin_name)
            if plugin and registry.validate_plugin(plugin):
                valid.append(f"{current_type}:{plugin_name}")
            else:
                invalid.append(f"{current_type}:{plugin_name}")

    return {"valid": valid, "invalid": invalid}
