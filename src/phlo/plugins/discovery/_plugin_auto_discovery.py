"""Auto-discovery toggle and bootstrap logic for plugins.

Auto-discovery is on by default and disabled by PHLO_NO_AUTO_DISCOVER or the
settings default. In regulated mode discovery failures abort startup;
otherwise they are logged as warnings so the CLI stays usable.
Internal helper of phlo.plugins.discovery, building on the package's plugin loading machinery
and phlo.config to gate auto-discovery at startup.
"""

from __future__ import annotations

from phlo.config import get_settings
from phlo.config.process import PluginDiscoverySettings
from phlo.logging import get_logger
from phlo.plugins.discovery._plugin_loading import discover_plugins

logger = get_logger(__name__)


def _strict_auto_discovery_enabled() -> bool:
    """Return True when auto-discovery failures must fail startup."""
    from phlo.security.mode import is_regulated

    return is_regulated()


def auto_discover() -> None:
    """Automatically discover and register all plugins."""
    strict = _strict_auto_discovery_enabled()
    try:
        discover_plugins(auto_register=True, strict=strict)
    except Exception:
        if strict:
            raise
        logger.warning("plugin_auto_discover_failed", exc_info=True)


def is_auto_discover_disabled_by_env() -> bool:
    """Return True when PHLO_NO_AUTO_DISCOVER explicitly disables discovery."""
    return PluginDiscoverySettings().no_auto_discover


def should_auto_discover() -> bool:
    """Resolve auto-discovery using settings default plus env override precedence."""
    settings = get_settings()
    return settings.plugins_auto_discover and not is_auto_discover_disabled_by_env()
