"""Transformation provider plugin classes.

TransformationProviderPlugin is the abstract contract core uses to talk to
transformation backends (dbt and similar) without importing them: only
metadata and the asset retriever are required; CLI, compiler, and manifest
hooks default to None. Concrete providers keep their imports lazy inside
methods so the base class stays dependency-free.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from phlo.plugins.base.plugin import Plugin, PluginMetadata


class TransformationProviderPlugin(Plugin, ABC):
    """Base class for transformation provider plugins.

    Transformation provider plugins supply the core transformation primitives:
    - The @phlo_transformation decorator (or similar)
    - Asset spec generation from transformation models
    - CLI integration for running transformations
    - Compilation and manifest capabilities

    Example:
        ```python
        from phlo.plugins.base import TransformationProviderPlugin, PluginMetadata

        class DbtTransformationProvider(TransformationProviderPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="dbt",
                    version="0.1.0",
                    description="dbt-based transformation provider",
                )

            def get_asset_retriever(self) -> Callable[[], list[Any]]:
                from phlo_dbt.assets import build_dbt_asset_specs
                return build_dbt_asset_specs

            def get_cli_plugin(self) -> Any:
                from phlo_dbt.cli_plugin import DbtCliPlugin
                return DbtCliPlugin
        ```

    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""

    @abstractmethod
    def get_asset_retriever(self) -> Callable[[], list[Any]]:
        """Return a function to retrieve transformation asset specs.

        Example:
            ```python
            def get_asset_retriever(self) -> Callable[[], list[Any]]:
                from phlo_dbt.assets import build_dbt_asset_specs
                return build_dbt_asset_specs
            ```
        """

    def get_cli_plugin(self) -> Any | None:
        """Return a CLI plugin class for transformation commands, or None if not available."""
        return None

    def get_compiler(self) -> Any | None:
        """Return a compiler function for the transformation, or None if not available."""
        return None

    def get_manifest_loader(self) -> Any | None:
        """Return a manifest loader function, or None if not available."""
        return None
