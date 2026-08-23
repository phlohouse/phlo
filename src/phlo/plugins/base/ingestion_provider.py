"""Ingestion provider plugin classes.

IngestionProviderPlugin is the abstract contract for packages that
supply ingestion primitives. Implementations must expose plugin
metadata, an ingestion decorator factory (get_decorator), and an
asset-retriever callable; imports of concrete backends are deferred to
method call time so the base class stays import-light.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from phlo.plugins.base.plugin import Plugin, PluginMetadata


class IngestionProviderPlugin(Plugin, ABC):
    """Base class for ingestion provider plugins.

    Ingestion provider plugins supply the core ingestion primitives:
    - The @phlo_ingestion decorator
    - Asset registration and retrieval
    - Source connectors and pipeline configurations

    Example:
        ```python
        from phlo.plugins.base import IngestionProviderPlugin, PluginMetadata

        class DLTIngestionProvider(IngestionProviderPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="dlt",
                    version="0.1.0",
                    description="DLT-based ingestion provider",
                )

            def get_decorator(self) -> Callable:
                from phlo_dlt import phlo_ingestion
                return phlo_ingestion

            def get_asset_retriever(self) -> Callable:
                from phlo_dlt import get_ingestion_assets
                return get_ingestion_assets
        ```

    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""

    @abstractmethod
    def get_decorator(self) -> Callable:
        """Return the ingestion decorator function.

        Example:
            ```python
            def get_decorator(self) -> Callable:
                from phlo_dlt import phlo_ingestion
                return phlo_ingestion
            ```

        """

    @abstractmethod
    def get_asset_retriever(self) -> Callable[[], list[Any]]:
        """Return a function to retrieve registered ingestion assets.

        Example:
            ```python
            def get_asset_retriever(self) -> Callable[[], list[Any]]:
                from phlo_dlt import get_ingestion_assets
                return get_ingestion_assets
            ```

        """
