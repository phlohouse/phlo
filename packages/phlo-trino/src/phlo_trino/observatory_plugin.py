"""Observatory extension for Trino data explorer UI.

Integrates Trino data exploration into the Phlo Observatory web UI via the
TrinoObservatoryExtension plugin.


Example:
    The plugin is automatically discovered and loaded by Observatory:
    >>> from phlo_trino.observatory_plugin import TrinoObservatoryExtension
    >>> ext = TrinoObservatoryExtension()
    >>> print(ext.manifest.name)
    trino


Loaded through the Observatory extension entry points at UI startup rather
than imported directly by other phlo modules.
"""

from __future__ import annotations

from importlib import resources
from importlib.abc import Traversable

from phlo.plugins import PluginMetadata
from phlo.plugins.observatory import (
    ObservatoryExtensionCompatibility,
    ObservatoryExtensionManifest,
    ObservatoryExtensionNavItem,
    ObservatoryExtensionPlugin,
    ObservatoryExtensionUI,
)

VERSION = "0.1.0"


class TrinoObservatoryExtension(ObservatoryExtensionPlugin):
    """Observatory extension metadata for Trino data explorer UI."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin identity, version, and description."""
        return PluginMetadata(
            name="trino",
            version=VERSION,
            description="Observatory UI extension for Trino data explorer",
        )

    @property
    def manifest(self) -> ObservatoryExtensionManifest:
        """Return the extension manifest defining UI navigation and compatibility."""
        return ObservatoryExtensionManifest(
            name="trino",
            version=VERSION,
            compat=ObservatoryExtensionCompatibility(observatory_min="0.1.0"),
            ui=ObservatoryExtensionUI(
                nav=[ObservatoryExtensionNavItem(title="Data Explorer", to="/extensions/trino")]
            ),
        )

    @property
    def asset_root(self) -> Traversable:
        """Return the package resource path for observatory static assets."""
        return resources.files("phlo_trino").joinpath("observatory_assets")
