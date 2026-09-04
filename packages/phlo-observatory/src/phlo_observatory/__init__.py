"""Phlo Observatory UI package.

The Observatory is Phlo's web-based UI for data observability, lineage visualization,
and system monitoring. This package provides the core infrastructure for the
Observatory web interface, including extension discovery, settings management,
and service orchestration.

Key Components:
    - ObservatoryExtensionPlugin: Base class for extending Observatory UI
    - ObservatorySettings: Configuration for the Observatory service
    - SettingsStore: Neutral storage contract for UI settings and preferences
    - ObservatoryServicePlugin: Service plugin for container orchestration

Example:
    >>> from phlo_observatory import ObservatorySettings, get_settings
    >>> settings = get_settings()
    >>> print(settings.observatory_settings_db_url)

See Also:
    - phlo.plugins.observatory: Extension API definitions
    - phlo.plugins.observatory_settings: Settings storage backend

"""

from phlo.plugins.observatory import (
    ObservatoryExtensionCompatibility,
    ObservatoryExtensionManifest,
    ObservatoryExtensionNavItem,
    ObservatoryExtensionPlugin,
    ObservatoryExtensionRoute,
    ObservatoryExtensionSettings,
    ObservatoryExtensionSettingsPanel,
    ObservatoryExtensionSlot,
    ObservatoryExtensionUI,
    discover_observatory_extensions,
    get_observatory_extension,
)
from phlo.plugins.observatory_settings import (
    SettingsRecord,
    SettingsScope,
    SettingsStore,
    get_settings_service,
)
from phlo_observatory.plugin import ObservatoryServicePlugin
from phlo_observatory.settings import ObservatorySettings, get_settings

__all__ = [
    "ObservatoryExtensionCompatibility",
    "ObservatoryExtensionManifest",
    "ObservatoryExtensionNavItem",
    "ObservatoryExtensionPlugin",
    "ObservatoryExtensionRoute",
    "ObservatoryExtensionSettings",
    "ObservatoryExtensionSettingsPanel",
    "ObservatoryExtensionSlot",
    "ObservatoryExtensionUI",
    "ObservatoryServicePlugin",
    "ObservatorySettings",
    "SettingsRecord",
    "SettingsScope",
    "SettingsStore",
    "discover_observatory_extensions",
    "get_observatory_extension",
    "get_settings",
    "get_settings_service",
]
from importlib.metadata import version

__version__ = version("phlo-observatory")
