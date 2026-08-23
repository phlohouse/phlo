"""Observatory extension for Loki logs UI.

Provides LokiObservatoryExtension, which registers a navigation item for
accessing logs and serves static UI assets. Loaded through the phlo plugin
entry-point mechanism at startup rather than imported directly; the
Observatory auto-discovers this extension via phlo.plugins.
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


class LokiObservatoryExtension(ObservatoryExtensionPlugin):
    """Observatory extension for Loki log aggregation UI.

    Adds navigation to the logs view and serves bundled static assets for
    the log UI components. Instantiated by the Observatory discovery system::

        extension = LokiObservatoryExtension()
        manifest = extension.manifest
        nav_items = manifest.ui.nav

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin identity metadata for discovery."""
        return PluginMetadata(
            name="loki",
            version="0.1.0",
            description="Observatory UI extension for logs",
        )

    @property
    def manifest(self) -> ObservatoryExtensionManifest:
        """Return the manifest declaring Observatory navigation and compatibility."""
        return ObservatoryExtensionManifest(
            name="loki",
            version="0.1.0",
            compat=ObservatoryExtensionCompatibility(observatory_min="0.1.0"),
            ui=ObservatoryExtensionUI(nav=[ObservatoryExtensionNavItem(title="Logs", to="/logs")]),
        )

    @property
    def asset_root(self) -> Traversable:
        """Return the package path to the bundled UI assets."""
        return resources.files("phlo_loki").joinpath("observatory_assets")
