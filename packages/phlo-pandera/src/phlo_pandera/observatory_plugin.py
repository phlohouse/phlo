"""Observatory extension for quality UI.

This module provides the PanderaObservatoryExtension class which integrates
the Phlo Quality Framework with the Observatory UI. It defines navigation items,
static assets, and metadata for the quality dashboard.

The extension adds a "Quality" navigation item to the Observatory UI that
provides access to quality check results, historical trends, and quality
metrics visualization.

Example:
    The extension is automatically discovered and loaded by the Observatory
    plugin system. No manual configuration is required beyond installing
    the phlo-pandera package.

Extension Assets:
    Static assets (HTML, JS, CSS) for the quality UI are located in:
    ``phlo_pandera/observatory_assets/``

See Also:
    - Observatory extension system documentation
    - ``phlo_observatory`` package for the base extension framework


    Pandera observatory extension, loaded via the phlo.plugins.observatory entry point at startup.
    Builds on the phlo.plugins.observatory extension interfaces.
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


class PanderaObservatoryExtension(ObservatoryExtensionPlugin):
    """Observatory extension exposing the Quality UI pages.

    Provides the metadata, manifest, and static assets for the quality
    dashboards. Discovered automatically via the
    ``phlo.observatory.extensions`` entry point; not instantiated directly.

    Example:
        The extension is typically not instantiated directly. Instead, it's
        loaded by the Observatory plugin system:

        ```python
        # In Observatory, extensions are discovered via entry points
        from phlo.plugins.observatory import load_extensions

        extensions = load_extensions()
        quality_ext = extensions.get("quality")
        ```

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata identifying the quality extension.

        Example:
            ```python
            ext = PanderaObservatoryExtension()
            meta = ext.metadata
            print(f"{meta.name} v{meta.version}")
            # Output: quality v0.1.0
            ```

        """
        return PluginMetadata(
            name="quality",
            version="0.1.0",
            description="Observatory UI extension for data quality",
        )

    @property
    def manifest(self) -> ObservatoryExtensionManifest:
        """Build the manifest defining quality navigation and compatibility.

        Example:
            ```python
            ext = PanderaObservatoryExtension()
            manifest = ext.manifest
            print(manifest.ui.nav[0].title)  # "Quality"
            print(manifest.ui.nav[0].to)     # "/extensions/quality"
            ```

        """
        return ObservatoryExtensionManifest(
            name="quality",
            version="0.1.0",
            compat=ObservatoryExtensionCompatibility(observatory_min="0.1.0"),
            ui=ObservatoryExtensionUI(
                nav=[ObservatoryExtensionNavItem(title="Quality", to="/extensions/quality")]
            ),
        )

    @property
    def asset_root(self) -> Traversable:
        """Return the packaged directory holding the quality UI static assets.

        Example:
            ```python
            ext = PanderaObservatoryExtension()
            assets = ext.asset_root

            # Access asset files
            index_html = assets.joinpath("index.html")
            if index_html.is_file():
                content = index_html.read_text()
            ```

        """
        return resources.files("phlo_pandera").joinpath("observatory_assets")
