"""Plugins that expose Phlo Transform to the Phlo capability system."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from phlo.capabilities import AssetSpec
from phlo.plugins.base import AssetProviderPlugin, PluginMetadata, TransformationProviderPlugin

from phlo_transform import __version__
from phlo_transform.assets import build_transform_asset_specs
from phlo_transform.cli_plugin import TransformCliPlugin


def _metadata() -> PluginMetadata:
    return PluginMetadata(
        name="transform",
        version=__version__,
        description="SQL transforms from phlo.transform.sql and Phlo Transform workspaces",
        homepage="https://github.com/phlohouse/phlo-transform",
    )


class TransformAssetProvider(AssetProviderPlugin):
    """Expose transform declarations and compiled workspace models as assets."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""

        return _metadata()

    def get_assets(self) -> Iterable[AssetSpec]:
        """Return every transform-backed asset spec."""

        return build_transform_asset_specs()


class TransformProvider(TransformationProviderPlugin):
    """Supply the ``transform`` transformation provider contract."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""

        return _metadata()

    def get_asset_retriever(self) -> Callable[[], list[AssetSpec]]:
        """Return the function that builds transform asset specs."""

        return build_transform_asset_specs

    def get_cli_plugin(self) -> type:
        """Return the ``phlo transform`` command plugin."""

        return TransformCliPlugin


__all__ = ["TransformAssetProvider", "TransformProvider"]
