"""Smoke tests for the example Observatory extension package.

Checks that the extension manifest builds with expected fields and that static
assets ship inside the package.
"""

from phlo.plugins.observatory import ObservatoryExtensionManifest
from phlo_observatory_example.observatory_plugin import ExampleObservatoryExtension


def test_example_extension_manifest_smoke() -> None:
    """Validate basic extension manifest fields."""
    plugin = ExampleObservatoryExtension()

    assert plugin.metadata.name == "example"
    assert isinstance(plugin.get_manifest(), ObservatoryExtensionManifest)
    assert plugin.manifest.name == "example"
    assert plugin.manifest.ui.routes[0].path == "/extensions/example"


def test_example_extension_assets_present() -> None:
    """Ensure extension static assets are packaged."""
    plugin = ExampleObservatoryExtension()

    assert plugin.asset_root.joinpath("example.js").is_file()
