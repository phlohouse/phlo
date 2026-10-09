"""Plugin providers for SQL transforms declared with ``phlo.transform.sql``."""

from collections.abc import Callable, Iterable

from phlo.capabilities import AssetSpec
from phlo.plugins.base import AssetProviderPlugin, PluginMetadata, TransformationProviderPlugin
from phlo.transform import get_transform_assets


def _metadata() -> PluginMetadata:
    return PluginMetadata(
        name="transform",
        version="0.1.0",
        description="SQL transforms declared with phlo.transform.sql",
    )


class TransformAssetProvider(AssetProviderPlugin):
    """Expose registered SQL transforms to orchestrator adapters."""

    @property
    def metadata(self) -> PluginMetadata:
        return _metadata()

    def get_assets(self) -> Iterable[AssetSpec]:
        return get_transform_assets()


class TransformProvider(TransformationProviderPlugin):
    """Expose Phlo's SQL transform authoring capability."""

    @property
    def metadata(self) -> PluginMetadata:
        return _metadata()

    def get_asset_retriever(self) -> Callable[[], list[AssetSpec]]:
        return get_transform_assets
