"""Register the pack's report asset with Phlo's asset discovery."""

from __future__ import annotations

from collections.abc import Iterable

from phlo.capabilities.specs import AssetSpec
from phlo.plugins.base import AssetProviderPlugin, PluginMetadata


class SourceToReportAssets(AssetProviderPlugin):
    """The preset's own asset provider: the report, after the dbt marts."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="source-to-report",
            version="0.1.0",
            description="Reference lakehouse: the air quality report asset",
        )

    def get_assets(self) -> Iterable[AssetSpec]:
        from workflows.analysis import ASSETS

        return list(ASSETS)
