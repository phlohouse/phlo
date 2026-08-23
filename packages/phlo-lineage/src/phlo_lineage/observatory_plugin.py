"""Observatory extension plugin for lineage graph UI.

This module provides the LineageObservatoryExtension class, which integrates
the phlo-lineage visualization features into the Observatory web UI. It registers
the lineage graph view as a navigation item and serves static assets.

Extension Features:
    - Lineage Graph navigation item in Observatory UI
    - Static assets for lineage visualization (JS, CSS, images)
    - Compatibility checking with Observatory core version

Plugin Registration:
    This extension is auto-discovered via entry points. The Observatory framework
    loads and initializes it automatically.

Asset Structure:
    Static assets are bundled in the package at:
        phlo_lineage/observatory_assets/

Example:
    Once loaded, users can navigate to /lineage in Observatory to view:
    - Interactive lineage graph visualization
    - Asset dependency relationships
    - Column-level lineage details

See Also:
    phlo.plugins.observatory for the extension plugin interface.
    phlo_lineage.graph for graph construction logic.


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


class LineageObservatoryExtension(ObservatoryExtensionPlugin):
    """Observatory extension metadata for lineage graph UI."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for extension discovery.

        Provides identity (``lineage`` v0.1.0) that the Observatory plugin loader
        uses to uniquely identify this extension, display its information in the UI,
        and detect duplicate or conflicting extensions.

        Example:
                    >>> ext = LineageObservatoryExtension()
                    >>> meta = ext.metadata
                    >>> print(f"Extension: {meta.name} v{meta.version}")
                    Extension: lineage v0.1.0

        """
        return PluginMetadata(
            name="lineage",
            version="0.1.0",
            description="Observatory UI extension for lineage graph",
        )

    @property
    def manifest(self) -> ObservatoryExtensionManifest:
        """Return the Observatory extension manifest for this extension.

        Declares the extension's UI integration points and version compatibility:
        name ``lineage`` v0.1.0 (matching metadata.name), requiring Observatory core
        >= 0.1.0 (loading in incompatible versions raises a warning), with a single
        navigation item "Lineage" pointing to "/lineage". See
        ObservatoryExtensionManifest for the full manifest schema.

        Example:
                    >>> ext = LineageObservatoryExtension()
                    >>> manifest = ext.manifest
                    >>> print(f"Requires Observatory >= {manifest.compat.observatory_min}")
                    >>> for item in manifest.ui.nav:
                    ...     print(f"Nav: {item.title} -> {item.to}")

        """
        return ObservatoryExtensionManifest(
            name="lineage",
            version="0.1.0",
            compat=ObservatoryExtensionCompatibility(observatory_min="0.1.0"),
            ui=ObservatoryExtensionUI(
                nav=[ObservatoryExtensionNavItem(title="Lineage", to="/lineage")]
            ),
        )

    @property
    def asset_root(self) -> Traversable:
        """Return the static asset directory bundled with the extension.

        Points at phlo_lineage/observatory_assets, which holds the JavaScript,
        CSS, images, and HTML templates served by Observatory at a URL derived from
        the extension name (e.g. /extensions/lineage/assets/). Uses
        importlib.resources so it works correctly in both development and installed
        wheel contexts.

        Example:
                    >>> ext = LineageObservatoryExtension()
                    >>> assets = ext.asset_root
                    >>> # List asset files
                    >>> for path in assets.iterdir():
                    ...     print(f"Asset: {path.name}")

        """
        return resources.files("phlo_lineage").joinpath("observatory_assets")
