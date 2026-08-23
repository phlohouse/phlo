"""Example Observatory extension plugin implementation.

This module defines the ExampleObservatoryExtension class, which demonstrates
the full capabilities of the Observatory extension API including:

- Custom UI routes and navigation items
- Dashboard slot integrations
- Extension settings panels with JSON schema validation
- Static asset serving for bundled JavaScript components

Classes:
    ExampleObservatoryExtension: Main extension plugin implementation.

Example:
    The extension is loaded automatically by the plugin discovery system::

        from phlo.plugins import discover_plugins
        plugins = discover_plugins("phlo.observatory.extensions")

See Also:
    phlo.plugins.observatory: Base classes and interfaces for Observatory extensions.
    phlo_observatory_example.observatory_assets: Bundled static assets.

Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Reference implementation built on the phlo.plugins.observatory extension API.
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
    ObservatoryExtensionRoute,
    ObservatoryExtensionSettings,
    ObservatoryExtensionSettingsPanel,
    ObservatoryExtensionSlot,
    ObservatoryExtensionUI,
)


class ExampleObservatoryExtension(ObservatoryExtensionPlugin):
    """Example Observatory extension demonstrating plugin capabilities.

    Registers a route at ``/extensions/example``, a sidebar navigation link,
    dashboard widget slots (after cards and hub stats), and a settings panel
    with toggle and message configuration.

    Example:
        Extension is auto-discovered and loaded by Phlo::

            from phlo.plugins.observatory import load_extension
            ext = load_extension("phlo_observatory_example")

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata used for extension discovery and registration.

        Example:
            Access metadata for debugging or display::

                ext = ExampleObservatoryExtension()
                print(ext.metadata.name)  # "example"

        """
        return PluginMetadata(
            name="example",
            version="0.1.0",
            description="Example Observatory UI extension",
        )

    @property
    def manifest(self) -> ObservatoryExtensionManifest:
        """Return the extension manifest: routes, navigation, slots, settings schema, and compat.

        Raises ValidationError when the manifest configuration is invalid.

        Example:
            Inspect manifest configuration::

                ext = ExampleObservatoryExtension()
                manifest = ext.manifest
                print(manifest.ui.routes)  # List of routes

        """
        return ObservatoryExtensionManifest(
            name="example",
            version="0.1.0",
            compat=ObservatoryExtensionCompatibility(observatory_min="0.1.0"),
            settings=ObservatoryExtensionSettings(
                settings_schema={
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "message": {"type": "string"},
                    },
                },
                defaults={"enabled": True, "message": "Hello from extension settings."},
                scope="extension",
            ),
            ui=ObservatoryExtensionUI(
                routes=[
                    ObservatoryExtensionRoute(
                        path="/extensions/example",
                        module="/example.js",
                        export="registerRoutes",
                    )
                ],
                nav=[ObservatoryExtensionNavItem(title="Example", to="/extensions/example")],
                slots=[
                    ObservatoryExtensionSlot(
                        slot_id="dashboard.after-cards",
                        module="/example.js",
                        export="registerDashboardSlot",
                    ),
                    ObservatoryExtensionSlot(
                        slot_id="hub.after-stats",
                        module="/example.js",
                        export="registerHubSlot",
                    ),
                ],
                settings=[
                    ObservatoryExtensionSettingsPanel(
                        module="/example.js", export="registerSettings"
                    )
                ],
            ),
        )

    @property
    def asset_root(self) -> Traversable:
        """Return the package path to the ``observatory_assets`` directory served to the frontend.

        Uses importlib.resources so resolution works across editable and
        wheel installs; raises ModuleNotFoundError when resources cannot be
        located.

        Example:
            Access bundled JavaScript file::

                ext = ExampleObservatoryExtension()
                js_path = ext.asset_root.joinpath("example.js")
                content = js_path.read_text()

        """
        return resources.files("phlo_observatory_example").joinpath("observatory_assets")
