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
from threading import RLock

from phlo.logging_context import get_logger
from phlo.plugins.base.plugin import Plugin
from phlo.plugins.discovery._plugin_constants import PLUGIN_FAMILIES, plugin_family
from phlo.plugins.discovery._registry_metadata import plugin_metadata_to_dict
from phlo.plugins.discovery._registry_validation import validate_plugin_interface

logger = get_logger(__name__)


class PluginRegistry:
    """Central registry for Phlo plugins, sharing one lock with lifecycle transitions."""

    def __init__(self):
        """Initialize empty plugin registry."""
        # ponytail: one registry lock; use per-registration locks if discovery throughput matters.
        self._lock = RLock()
        self._plugins: dict[str, dict[str, Plugin]] = {family: {} for family in PLUGIN_FAMILIES}
        self._all_plugins: dict[str, Plugin] = {}

    def register(self, family: str, plugin: Plugin, replace: bool = False) -> None:
        """Publish an initialized plugin in a canonical family.

        Discovery owns initialization and teardown through register_plugin_with_lifecycle.
        """
        with self._lock:
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
            # Both indexes move together under the registry's lifecycle lock.
            plugin_dict[name] = plugin
            self._all_plugins[f"{definition.key_prefix}:{name}"] = plugin
            logger.debug("plugin_registered", plugin_type=family, plugin_name=name, replace=replace)

    def get(self, family: str, name: str) -> Plugin | None:
        """Return a plugin by family and name."""
        with self._lock:
            plugin_family(family)
            return self._plugins[family].get(name)

    def list(self, family: str) -> builtins.list[str]:
        """List registered plugin names for one family."""
        with self._lock:
            plugin_family(family)
            return list(self._plugins[family])

    def remove(self, family: str, name: str) -> None:
        """Remove a plugin by family and name without cleaning the instance."""
        with self._lock:
            definition = plugin_family(family)
            self._plugins[family].pop(name, None)
            self._all_plugins.pop(f"{definition.key_prefix}:{name}", None)

    def list_all_plugins(self) -> dict[str, builtins.list[str]]:
        """List all registered plugins by canonical family."""
        with self._lock:
            return {family: self.list(family) for family in PLUGIN_FAMILIES}

    def clear(self) -> None:
        """Withdraw all plugins before cleanup, once per instance, even on failure."""
        with self._lock:
            plugins = list(self._all_plugins.items())
            for plugin_dict in self._plugins.values():
                plugin_dict.clear()
            self._all_plugins.clear()
            cleaned = 0
            cleanup_failures = 0
            cleaned_plugin_ids: set[int] = set()
            for plugin_key, plugin in plugins:
                if id(plugin) in cleaned_plugin_ids:
                    continue
                cleaned_plugin_ids.add(id(plugin))
                try:
                    plugin.cleanup()
                    cleaned += 1
                except Exception:
                    cleanup_failures += 1
                    logger.warning("plugin_cleanup_failed", plugin_key=plugin_key, exc_info=True)
            logger.debug(
                "plugin_registry_cleared",
                previous_total=len(plugins),
                unique_plugins=len(cleaned_plugin_ids),
                cleaned_plugins=cleaned,
                cleanup_failures=cleanup_failures,
            )

    def iter_plugins(self) -> builtins.list[Plugin]:
        """Return a snapshot of all registered plugin instances."""
        with self._lock:
            return list(self._all_plugins.values())

    def __len__(self) -> int:
        """Return total number of registered plugins."""
        with self._lock:
            return len(self._all_plugins)

    def __contains__(self, key: str) -> bool:
        """Check if a plugin is registered by key, e.g. ``source:name``."""
        with self._lock:
            return key in self._all_plugins

    def get_plugin_metadata(self, family: str, name: str) -> dict | None:
        """Get metadata for a plugin by family and name."""
        with self._lock:
            plugin = self.get(family, name)
            return plugin_metadata_to_dict(plugin) if plugin else None

    def validate_plugin(self, plugin: Plugin) -> bool:
        """Validate plugin interface compliance."""
        return validate_plugin_interface(plugin, logger)


_GLOBAL_REGISTRY = PluginRegistry()


def get_global_registry() -> PluginRegistry:
    """Get the global plugin registry instance."""
    return _GLOBAL_REGISTRY


def reset_global_registry() -> None:
    """Reset the global registry for tests."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = PluginRegistry()
