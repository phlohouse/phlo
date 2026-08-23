"""Null check plugin for validating column completeness.

This module provides the NullCheckPlugin, which enables validation of
null value presence in specified columns. It helps ensure data completeness
by detecting missing values and enforcing thresholds for acceptable null rates.

Example:
    Using the null check plugin::

        from phlo_core.quality.null_check import NullCheckPlugin

        # Create the plugin
        plugin = NullCheckPlugin()

        # Strict null check (no nulls allowed)
        strict_check = plugin.create_check(
            columns=["id", "email", "created_at"],
            allow_threshold=0.0
        )

        # Lenient null check (allow up to 10% nulls in optional fields)
        lenient_check = plugin.create_check(
            columns=["middle_name", "phone_number"],
            allow_threshold=0.10
        )

        # Mixed columns with different requirements
        mixed_check = plugin.create_check(
            columns=["required_field", "optional_field"],
            allow_threshold=0.0  # Applies to all columns
        )

"""

from typing import Any

from phlo.plugins import PluginMetadata, QualityCheckPlugin


class NullCheckPlugin(QualityCheckPlugin[Any]):
    """Plugin for performing null value validation on data columns.

    Creates NullCheck instances that validate whether specified columns
    contain null values within acceptable thresholds, supporting both
    strict validation (no nulls) and a configurable per-column null
    percentage. Useful for required-field completeness checks, data-quality
    gates in pipelines, and completeness SLAs.

    Example:
            Strict null check for required fields::

                from phlo_core.quality.null_check import NullCheckPlugin

                plugin = NullCheckPlugin()
                check = plugin.create_check(
                    columns=["user_id", "email", "registration_date"],
                    allow_threshold=0.0  # No nulls allowed
                )

            Lenient null check for optional fields::

                check = plugin.create_check(
                    columns=["phone", "address_line_2"],
                    allow_threshold=0.20  # Allow up to 20% nulls
                )

            Using with Pandera schema::

                import pandera as pa

                schema = pa.DataFrameSchema(
                    columns={
                        "id": pa.Column(pa.Int64, checks=check),
                        "name": pa.Column(pa.String, checks=check),
                    }
                )

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the null-check quality plugin."""
        return PluginMetadata(
            name="null_check",
            version="0.1.0",
            description="Null checks for column completeness",
            author="Phlo Team",
            tags=["quality", "nulls"],
        )

    def create_check(self, *args: Any, **kwargs: Any) -> Any:
        """Create a null check instance.

        Creates a configured phlo_pandera NullCheck from ``columns`` (each
        checked individually) and ``allow_threshold`` (maximum null ratio
        per column, default 0.0). Raises TypeError for malformed arguments.
        The result validates DataFrames directly or inside Pandera schemas.
        """
        if len(args) > 2 or set(kwargs) - {"columns", "allow_threshold"}:
            raise TypeError("create_check accepts columns and allow_threshold")
        columns = kwargs.get("columns", args[0] if args else None)
        allow_threshold = kwargs.get("allow_threshold", args[1] if len(args) > 1 else 0.0)
        if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
            raise TypeError("columns must be a list of strings")
        if not isinstance(allow_threshold, (int, float)):
            raise TypeError("allow_threshold must be numeric")

        from phlo_pandera.checks import NullCheck

        return NullCheck(columns=columns, allow_threshold=allow_threshold)
