"""Catalog plugin classes.

This module defines plugin types for catalog configuration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from phlo.plugins.base.plugin import Plugin


class CatalogPlugin(Plugin, ABC):
    """Base class for engine-agnostic catalog plugins.

    Catalog plugins define logical catalog configuration that engine adapters
    serialize into their native formats (files or programmatic config).

    Example:
        ```python
        class ExampleCatalogPlugin(CatalogPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="example",
                    version="1.0.0",
                    description="Example catalog plugin",
                )

            @property
            def targets(self) -> list[str]:
                return ["engine-a", "engine-b"]

            @property
            def catalog_name(self) -> str:
                return "example"

            def get_properties(self) -> dict[str, str]:
                return {
                    "catalog.type": "example",
                    "catalog.uri": "http://catalog:1234",
                }
        ```

    """

    @property
    @abstractmethod
    def targets(self) -> list[str]:
        """Return engine identifiers that can consume this catalog.

        Examples: ["trino"], ["spark"], ["trino", "spark"].
        """

    @property
    @abstractmethod
    def catalog_name(self) -> str:
        """Return the catalog identifier used by the engine."""

    @abstractmethod
    def get_properties(self) -> dict[str, Any]:
        """Return catalog configuration as a key-to-value dictionary."""

    def supports_target(self, target: str) -> bool:
        """Return True if the catalog supports the requested engine target."""
        return target in self.targets
