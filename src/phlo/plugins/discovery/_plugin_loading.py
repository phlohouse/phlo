"""Plugin entry-point discovery and loading.

Scans entry-point groups by plugin type, applies the configured
blacklist/whitelist, and validates each loaded object against its
expected type. In strict mode load failures raise PluginDiscoveryError;
otherwise they are recorded to the failure sink or logged.

Imported by the phlo.plugins.discovery internals: the package __init__,
auto-discovery, service loading, and the manifest resolver.
"""

from __future__ import annotations

from phlo.config import get_settings
from phlo.logging import get_logger, suppress_log_routing
from phlo.plugins.base import Plugin
from phlo.plugins.discovery._entry_points import entry_points_for_group
from phlo.plugins.discovery._plugin_constants import ENTRY_POINT_GROUPS, PLUGIN_EXPECTED_TYPES
from phlo.plugins.discovery._plugin_lifecycle import register_plugin_with_lifecycle

logger = get_logger(__name__)


class PluginDiscoveryError(RuntimeError):
    """Raised when strict plugin discovery cannot load an entry point."""

    def __init__(
        self,
        *,
        plugin_name: str,
        entry_point: str,
        plugin_type: str,
        reason: str = "load_failed",
    ) -> None:
        self.plugin_name = plugin_name
        self.entry_point = entry_point
        self.plugin_type = plugin_type
        self.reason = reason
        super().__init__(
            f"Failed to discover plugin {plugin_name!r} from {entry_point!r} "
            f"for plugin type {plugin_type!r}"
        )


def is_plugin_allowed(plugin_name: str) -> bool:
    """Check if a plugin is allowed based on whitelist/blacklist configuration."""
    settings = get_settings()

    if plugin_name in settings.plugins_blacklist:
        logger.debug("plugin_blacklisted_skipping", plugin_name=plugin_name)
        return False

    if settings.plugins_whitelist and plugin_name not in settings.plugins_whitelist:
        logger.debug("plugin_not_whitelisted_skipping", plugin_name=plugin_name)
        return False

    return True


def discover_plugins(
    plugin_type: str | None = None,
    auto_register: bool = True,
    *,
    failure_level: str = "error",
    failure_sink: list[dict[str, str]] | None = None,
    strict: bool = False,
) -> dict[str, list[Plugin]]:
    """Discover installed Phlo plugins from entry points.

    Per-entry-point failures are isolated: a plugin that fails to load, is
    blacklisted, or has the wrong type never aborts the scan. In strict mode
    the first failure raises :class:`PluginDiscoveryError`; otherwise the error
    is logged at ``failure_level`` and, when ``failure_sink`` is given, also
    appended there for callers to surface. With ``auto_register=True`` each
    successfully loaded plugin is initialized immediately via
    :func:`register_plugin_with_lifecycle` (replace=True), so registration and
    initialization happen per family as discovery proceeds.
    """
    settings = get_settings()

    with suppress_log_routing():
        if not settings.plugins_enabled:
            logger.info("Plugin system is disabled")
            return {key: [] for key in ENTRY_POINT_GROUPS}

        discovered: dict[str, list[Plugin]] = {key: [] for key in ENTRY_POINT_GROUPS}
        types_to_discover = [plugin_type] if plugin_type else list(ENTRY_POINT_GROUPS)

        for current_type in types_to_discover:
            if current_type not in ENTRY_POINT_GROUPS:
                logger.warning("unknown_plugin_type", plugin_type=current_type)
                continue

            entry_point_group = ENTRY_POINT_GROUPS[current_type]

            logger.info(
                "plugin_discovery_started",
                plugin_type=current_type,
                entry_point_group=entry_point_group,
            )

            entry_points = entry_points_for_group(entry_point_group)

            for entry_point in entry_points:
                try:
                    if not is_plugin_allowed(entry_point.name):
                        continue

                    logger.info(
                        "plugin_loading",
                        plugin_name=entry_point.name,
                        entry_point=entry_point.value,
                    )

                    plugin_candidate = entry_point.load()
                    plugin = (
                        plugin_candidate()
                        if isinstance(plugin_candidate, type)
                        else plugin_candidate
                    )

                    if not isinstance(plugin, Plugin):
                        logger.error(
                            "plugin_invalid_base_class",
                            plugin_name=entry_point.name,
                        )
                        if strict:
                            raise PluginDiscoveryError(
                                plugin_name=entry_point.name,
                                entry_point=entry_point.value,
                                plugin_type=current_type,
                                reason="invalid_base_class",
                            )
                        continue

                    expected_type = PLUGIN_EXPECTED_TYPES[current_type]
                    if not isinstance(plugin, expected_type):
                        logger.error(
                            "plugin_incorrect_type",
                            plugin_name=entry_point.name,
                            expected_type=expected_type.__name__,
                            actual_type=type(plugin).__name__,
                        )
                        if strict:
                            raise PluginDiscoveryError(
                                plugin_name=entry_point.name,
                                entry_point=entry_point.value,
                                plugin_type=current_type,
                                reason="incorrect_type",
                            )
                        continue

                    if auto_register:
                        register_plugin_with_lifecycle(current_type, plugin, replace=True)

                    discovered[current_type].append(plugin)

                    logger.debug(
                        "plugin_loaded",
                        plugin_name=plugin.metadata.name,
                        plugin_version=plugin.metadata.version,
                        plugin_type=current_type,
                    )
                except PluginDiscoveryError:
                    raise
                except Exception as exc:
                    if failure_sink is not None:
                        failure_sink.append(
                            {
                                "plugin_name": entry_point.name,
                                "entry_point": entry_point.value,
                                "plugin_type": current_type,
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            }
                        )
                    log_method = getattr(logger, failure_level, logger.error)
                    log_method(
                        "plugin_load_failed",
                        plugin_name=entry_point.name,
                        entry_point=entry_point.value,
                        plugin_type=current_type,
                        exc_info=True,
                    )
                    if strict:
                        raise PluginDiscoveryError(
                            plugin_name=entry_point.name,
                            entry_point=entry_point.value,
                            plugin_type=current_type,
                        ) from exc
                    continue

        total = sum(len(plugins) for plugins in discovered.values())
        logger.debug("plugin_discovery_completed", total_plugins=total, discovered=discovered)

        return discovered
