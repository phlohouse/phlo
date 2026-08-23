"""Quality provider plugin classes.

QualityProviderPlugin is the abstract extension point for quality engines:
it supplies the check decorator, check classes, schema extraction, and schema
module rendering used when scaffolding typed quality schemas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, TypeVar

from phlo.plugins.base.plugin import Plugin

TQualityCheck = TypeVar("TQualityCheck")


class QualityProviderPlugin(Plugin, ABC):
    """Base class for quality provider plugins.

    Quality provider plugins supply the core quality primitives:
    - The @phlo_quality decorator
    - Built-in check classes (NullCheck, RangeCheck, etc.)
    - Schema extraction capabilities

    Example:
        ```python
        from phlo.plugins.base import QualityProviderPlugin, PluginMetadata

        class PanderaQualityProvider(QualityProviderPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="pandera",
                    version="0.1.0",
                    description="Pandera-based quality provider",
                )

            def get_decorator(self) -> Callable:
                from phlo_pandera import phlo_quality
                return phlo_quality

            def get_check_classes(self) -> dict[str, type]:
                from phlo_pandera import (
                    NullCheck, RangeCheck, FreshnessCheck,
                    UniqueCheck, CountCheck, SchemaCheck,
                )
                return {
                    "null": NullCheck,
                    "range": RangeCheck,
                    "freshness": FreshnessCheck,
                    "unique": UniqueCheck,
                    "count": CountCheck,
                    "schema": SchemaCheck,
                }

            def get_schema_extractor(self) -> Any:
                from phlo_pandera import PanderaSchemaExtractor
                return PanderaSchemaExtractor
        ```

    """

    @abstractmethod
    def get_decorator(self) -> Callable:
        """Return the quality decorator function, such as ``@phlo_quality``.

        Example:
            ```python
            def get_decorator(self) -> Callable:
                from phlo_pandera import phlo_quality
                return phlo_quality
            ```
        """

    @abstractmethod
    def get_check_classes(self) -> dict[str, type]:
        """Return a mapping of check type short names to check classes.

        Example:
            ```python
            def get_check_classes(self) -> dict[str, type]:
                from phlo_pandera import NullCheck, RangeCheck
                return {"null": NullCheck, "range": RangeCheck}
            ```
        """

    def get_schema_extractor(self) -> Any | None:
        """Return the schema extractor class for converting native schemas, or None."""
        return None

    def get_schema_base_import(self) -> tuple[str, str] | None:
        """Return ``(module, symbol)`` for the generated-schema base class, or None."""
        return None

    def render_schema_field(
        self,
        *,
        name: str,
        type_name: str,
        nullable: bool,
        description: str | None = None,
    ) -> str | None:
        """Render one class-level field declaration as source.

        Return None when the provider does not support schema scaffolding.
        """
        return None

    def render_schema_module(
        self,
        *,
        domain: str,
        schema_class: str,
        type_imports: str,
        schema_fields: str,
    ) -> str | None:
        """Render a complete generated schema module as source.

        Return None when the provider does not support schema scaffolding.
        """
        return None

    def get_reconciliation_checks(self) -> dict[str, type] | None:
        """Return reconciliation check classes mapped by name, or None."""
        return None

    def build_checks_from_rules(self, rules: list[Any]) -> list[Any] | None:
        """Translate provider-neutral quality rules into provider-native checks.

        Returning None means this provider does not support neutral rule
        translation.
        """
        return None
