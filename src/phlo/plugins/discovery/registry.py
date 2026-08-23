"""Plugin registry for managing loaded plugins by canonical family.

Plugins are keyed family:name; families must be canonical and names are
unique within a family (replace=False rejects collisions). clear()
invokes each plugin's cleanup exactly once. A process-wide singleton is
exposed via get_global_registry, with reset_global_registry for tests.
Core of the phlo.plugins.discovery package: imported by its lifecycle, query,
service-loading, and manifest modules; hosts the global PluginRegistry singleton.
"""

from __future__ import annotations

import builtins

from phlo.logging import get_logger
from phlo.plugins.base import Plugin
from phlo.plugins.discovery._plugin_constants import PLUGIN_FAMILIES, plugin_family
from phlo.plugins.discovery._registry_metadata import plugin_metadata_to_dict
from phlo.plugins.discovery._registry_validation import validate_plugin_interface

logger = get_logger(__name__)


class PluginRegistry:
    """Central registry for Phlo plugins."""

    def __init__(self):
        """Initialize empty plugin registry."""
        self._plugins: dict[str, dict[str, Plugin]] = {family: {} for family in PLUGIN_FAMILIES}
        self._all_plugins: dict[str, Plugin] = {}

    def register(self, family: str, plugin: Plugin, replace: bool = False) -> None:
        """Register a plugin in a canonical family."""
        definition = plugin_family(family)
        if not isinstance(plugin, definition.plugin_type):
            raise TypeError(
                f"Plugin family '{family}' expects {definition.plugin_type.__name__}, "
                f"got {type(plugin).__name__}."
            )

        name = plugin.metadata.name
        plugin_dict = self._plugins[family]
        if name in plugin_dict and not replace:
            logger.warning("plugin_registration_conflict", plugin_type=family, plugin_name=name)
            raise ValueError(
                f"{definition.label} plugin '{name}' is already registered. "
                "Use replace=True to overwrite."
            )

        # Plugins are indexed twice: per family for typed lookup, and once in a
        # flat map keyed "key_prefix:name" for iteration and __contains__. Both
        # entries must move together in register/remove.
        plugin_dict[name] = plugin
        self._all_plugins[f"{definition.key_prefix}:{name}"] = plugin
        logger.debug("plugin_registered", plugin_type=family, plugin_name=name, replace=replace)

    def get(self, family: str, name: str) -> Plugin | None:
        """Return a plugin by family and name."""
        plugin_family(family)
        return self._plugins[family].get(name)

    def list(self, family: str) -> builtins.list[str]:
        """List registered plugin names for one family."""
        plugin_family(family)
        return list(self._plugins[family])

    def remove(self, family: str, name: str) -> None:
        """Remove a plugin by family and name."""
        definition = plugin_family(family)
        self._plugins[family].pop(name, None)
        self._all_plugins.pop(f"{definition.key_prefix}:{name}", None)

    def list_all_plugins(self) -> dict[str, builtins.list[str]]:
        """List all registered plugins by canonical family."""
        return {family: self.list(family) for family in PLUGIN_FAMILIES}

    def clear(self) -> None:
        """Clear all registered plugins and call cleanup once per plugin instance."""
        total = len(self._all_plugins)
        cleaned = 0
        cleanup_failures = 0
        # The same instance can appear in both its family map and _all_plugins;
        # dedup by object identity so cleanup() runs at most once per instance.
        cleaned_plugin_ids: set[int] = set()

        for plugin_key, plugin in list(self._all_plugins.items()):
            plugin_id = id(plugin)
            if plugin_id in cleaned_plugin_ids:
                continue
            cleaned_plugin_ids.add(plugin_id)
            try:
                plugin.cleanup()
                cleaned += 1
            except Exception:
                cleanup_failures += 1
                logger.warning("plugin_cleanup_failed", plugin_key=plugin_key, exc_info=True)

        for plugin_dict in self._plugins.values():
            plugin_dict.clear()
        self._all_plugins.clear()
        logger.debug(
            "plugin_registry_cleared",
            previous_total=total,
            unique_plugins=len(cleaned_plugin_ids),
            cleaned_plugins=cleaned,
            cleanup_failures=cleanup_failures,
        )

    def iter_plugins(self) -> builtins.list[Plugin]:
        """Return all registered plugin instances."""
        return list(self._all_plugins.values())

    def __len__(self) -> int:
        """Return total number of registered plugins."""
        return len(self._all_plugins)

    def __contains__(self, key: str) -> bool:
        """Check if a plugin is registered by key, e.g. ``source:name``."""
        return key in self._all_plugins

    def get_plugin_metadata(self, family: str, name: str) -> dict | None:
        """Get metadata for a plugin by family and name."""
        plugin = self.get(family, name)
        if not plugin:
            return None
        return plugin_metadata_to_dict(plugin)

    def validate_plugin(self, plugin: Plugin) -> bool:
        """Validate plugin interface compliance."""
        return validate_plugin_interface(plugin, logger)


_GLOBAL_REGISTRY = PluginRegistry()


def get_global_registry() -> PluginRegistry:
    """Get the global plugin registry instance."""
    return _GLOBAL_REGISTRY


def reset_global_registry() -> None:
    """Reset the global registry."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = PluginRegistry()
