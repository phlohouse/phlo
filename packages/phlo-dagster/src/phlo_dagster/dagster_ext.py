"""Dagster extension plugin classes for Phlo.

This module defines the plugin architecture for extending Dagster functionality
within Phlo. It provides base classes for plugins that contribute Dagster
definitions, resources, and custom functionality to the orchestration layer.

Plugin Architecture:
    - DagsterExtensionPlugin: Base class for all Dagster extensions
    - IngestionEnginePlugin: Deprecated base for ingestion plugins

    Plugins are discovered via entry_points (group: phlo.plugins.dagster) and
    automatically merged into global definitions.

Extension Points:
    - get_definitions(): Return Dagster definitions to merge
    - get_exports(): Expose symbols to phlo.* public API
    - clear_registries(): Clean up for reloads and testing

Registration:
    Plugins register via setuptools entry_points::

        [phlo.plugins.dagster]
        my_plugin = my_package.plugin:MyExtensionPlugin

Lifecycle:
    1. Discovery via entry_points
    2. Instantiation and type validation
    3. get_definitions() called during framework initialization
    4. Definitions merged into global Definitions object

Example:
    Creating a custom extension::

        from phlo_dagster.dagster_ext import DagsterExtensionPlugin
        import dagster as dg

        class MyExtension(DagsterExtensionPlugin):
            def get_definitions(self):
                @dg.asset
                def my_custom_asset():
                    return "data"

                return dg.Definitions(assets=[my_custom_asset])

"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable

from phlo.logging import get_logger
from phlo.plugins.base.plugin import Plugin

logger = get_logger(__name__)


class DagsterExtensionPlugin(Plugin, ABC):
    """
    Base class for Dagster extension plugins.

    These plugins contribute Dagster definitions (assets/resources/schedules/sensors/etc.)
    to the running Phlo instance.
    """

    def get_definitions(self) -> Any:
        """Return Dagster definitions to merge into the global Definitions."""
        try:
            import dagster as dg
        except Exception as exc:  # noqa: BLE001 - optional dependency
            logger.error(
                "dagster_extension_definitions_import_failed",
                plugin_class=self.__class__.__name__,
                exc_info=True,
            )
            raise RuntimeError("Dagster is required for DagsterExtensionPlugin") from exc
        return dg.Definitions()

    def get_exports(self) -> dict[str, Any]:
        """
        Return exported symbols to attach to the `phlo` public API.

        Example: {"ingestion": phlo_ingestion}
        """
        return {}

    def clear_registries(self) -> None:
        """
        Clear any global registries used by this plugin (primarily for module reload and tests).
        """
        ...


class IngestionEnginePlugin(DagsterExtensionPlugin, ABC):
    """Base class for ingestion engine capability plugins.

    Deprecated in favor of capability specs + orchestrator adapters.
    """

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Warn on subclassing to signal deprecation."""
        super().__init_subclass__(**kwargs)
        warnings.warn(
            "IngestionEnginePlugin is deprecated; use capability specs instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @abstractmethod
    def get_ingestion_assets(self) -> Iterable[Any]:
        """Return Dagster assets created by the ingestion engine."""
        ...

    @abstractmethod
    def get_ingestion_decorator(self) -> Callable[..., Any]:
        """Return the decorator used to define ingestion assets."""
        ...
