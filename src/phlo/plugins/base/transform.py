"""Transformation plugin classes.

This module defines plugin types for custom data transformations.

Deprecated: the legacy ``transformation`` SDK family has no bundled
implementation. Subclassing ``TransformationPlugin`` emits a
DeprecationWarning; new integrations should use asset-provider plugins. The
family stays discoverable, scaffoldable, and importable as a community-tier
(``legacy_verified``) surface.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from typing import Any

from phlo.plugins.base.plugin import Plugin


class TransformationPlugin(Plugin, ABC):
    """Base class for transformation plugins.

    Transformation plugins enable custom data processing steps
    that can be composed in data pipelines.

    Deprecated: subclassing emits a DeprecationWarning; use asset-provider
    plugins for new integrations.

    Example:
        ```python
        class PivotTransform(TransformationPlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="pivot",
                    version="1.0.0",
                    description="Pivot table transformation",
                )

            def transform(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
                index = config["index"]
                columns = config["columns"]
                values = config["values"]

                return df.pivot_table(
                    index=index,
                    columns=columns,
                    values=values,
                    aggfunc=config.get("aggfunc", "mean")
                )

            def get_output_schema(self, input_schema: dict, config: dict) -> dict:
                # Return schema of transformed data
                return {...}
        ```

    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        warnings.warn(
            "TransformationPlugin is deprecated and will be removed in an "
            "upcoming release: the legacy transformation SDK family has no "
            "bundled implementation. Build asset-provider plugins for new "
            "integrations.",
            DeprecationWarning,
            stacklevel=2,
        )

    @abstractmethod
    def transform(self, df: Any, config: dict[str, Any]) -> Any:
        """Transform a DataFrame.
        Example:
            ```python
            def transform(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
                column = config["column"]
                multiplier = config.get("multiplier", 1.0)

                df = df.copy()
                df[column] = df[column] * multiplier
                return df
            ```
        """

    def get_output_schema(
        self, input_schema: dict[str, str], config: dict[str, Any]
    ) -> dict[str, str] | None:
        """Get the schema of transformed data.
        This method is optional but recommended for type inference.
        """
        return None

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate transformation configuration.
        This method is optional but recommended for catching errors early.
        """
        return True
