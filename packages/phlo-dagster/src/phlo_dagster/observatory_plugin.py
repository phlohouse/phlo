"""Observatory extension for Dagster lineage UI.

This module provides an ObservatoryExtensionPlugin that exposes Dagster
asset information to Phlo's Observatory web UI. It registers the extension
with the Observatory plugin system and provides static assets for the
Dagster lineage view.

Observatory Integration:
    The DagsterObservatoryExtension implements the ObservatoryExtensionPlugin
    interface to contribute:
    - Extension metadata (name, version, compatibility)
    - Navigation items for the Observatory UI
    - Static assets (HTML, JS, CSS) for the lineage view

Extension Points:
    - metadata: Plugin identity for discovery
    - manifest: Extension manifest with navigation and compatibility
    - asset_root: Package path to bundled UI assets

UI Assets:
    Static assets are bundled in the package under observatory_assets/ and
    served through the Observatory's static file handling.

Navigation:
    The extension adds a "Lineage" navigation item linking to /lineage.

Example:
    Extension registration via entry_points::

        [phlo.plugins.observatory]
        dagster = phlo_dagster.observatory_plugin:DagsterObservatoryExtension

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Contributes a lineage UI extension to phlo.plugins.observatory.
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


class DagsterObservatoryExtension(ObservatoryExtensionPlugin):
    """Observatory extension metadata for Dagster lineage UI."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin identity metadata for discovery."""
        return PluginMetadata(
            name="dagster",
            version="0.1.0",
            description="Observatory UI extension for Dagster lineage",
        )

    @property
    def manifest(self) -> ObservatoryExtensionManifest:
        """Return extension manifest for Observatory navigation and compatibility."""
        return ObservatoryExtensionManifest(
            name="dagster",
            version="0.1.0",
            compat=ObservatoryExtensionCompatibility(observatory_min="0.1.0"),
            ui=ObservatoryExtensionUI(
                nav=[ObservatoryExtensionNavItem(title="Lineage", to="/lineage")]
            ),
        )

    @property
    def asset_root(self) -> Traversable:
        """Return package path to static observatory extension assets."""
        return resources.files("phlo_dagster").joinpath("observatory_assets")
