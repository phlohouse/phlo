"""Uniqueness check plugin for validating primary key integrity.

This module provides the UniquenessCheckPlugin, which enables validation of
uniqueness constraints on specified columns. It helps ensure data integrity
by detecting duplicate values in columns that should contain unique identifiers,
such as primary keys or natural keys.

Example:
    Using the uniqueness check plugin::

        from phlo_core.quality.uniqueness_check import UniquenessCheckPlugin

        # Create the plugin
        plugin = UniquenessCheckPlugin()

        # Strict uniqueness check (no duplicates allowed)
        strict_check = plugin.create_check(
            columns=["user_id"],
            allow_threshold=0.0
        )

        # Lenient uniqueness check (allow up to 5% duplicates)
        lenient_check = plugin.create_check(
            columns=["session_id"],
            allow_threshold=0.05
        )

        # Multi-column uniqueness check
        composite_check = plugin.create_check(
            columns=["first_name", "last_name", "date_of_birth"],
            allow_threshold=0.0
        )

"""

from typing import Any

from phlo.plugins import PluginMetadata, QualityCheckPlugin


class UniquenessCheckPlugin(QualityCheckPlugin[Any]):
    """Plugin for performing uniqueness validation on data columns.

    This plugin creates uniqueness check instances that validate whether
    specified columns contain unique values. It supports both strict uniqueness
    (no duplicates allowed) and lenient uniqueness (allows a configurable
    percentage of duplicates).

    The uniqueness check is particularly useful for:
        - Validating primary key integrity
        - Detecting duplicate records in data imports
        - Ensuring natural key uniqueness
        - Validating composite keys across multiple columns

    The metadata attribute carries the plugin's PluginMetadata.

    Example:
        Strict uniqueness check for primary keys::

            from phlo_core.quality.uniqueness_check import UniquenessCheckPlugin

            plugin = UniquenessCheckPlugin()
            check = plugin.create_check(
                columns=["customer_id"],
                allow_threshold=0.0  # No duplicates allowed
            )

        Lenient uniqueness check with duplicate tolerance::

            check = plugin.create_check(
                columns=["transaction_id"],
                allow_threshold=0.01  # Allow up to 1% duplicates
            )

        Composite uniqueness check::

            check = plugin.create_check(
                columns=["product_id", "warehouse_id"],
                allow_threshold=0.0
            )

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return PluginMetadata for the uniqueness-check plugin."""
        return PluginMetadata(
            name="uniqueness_check",
            version="0.1.0",
            description="Uniqueness validation for primary keys",
            author="Phlo Team",
            tags=["quality", "uniqueness"],
        )

    def create_check(self, *args: Any, **kwargs: Any) -> Any:
        """Create a configured UniqueCheck (from phlo_pandera) that validates
        uniqueness of columns — one column for simple checks, several for
        composite keys. allow_threshold (default 0.0) is the tolerated
        duplicate-row ratio between 0.0 and 1.0; TypeError for malformed
        arguments.

        Example:
            Strict uniqueness check on single column::

                from phlo_core.quality.uniqueness_check import UniquenessCheckPlugin

                plugin = UniquenessCheckPlugin()
                check = plugin.create_check(
                    columns=["order_id"],
                    allow_threshold=0.0
                )

            Allow some duplicates::

                check = plugin.create_check(
                    columns=["session_id"],
                    allow_threshold=0.02  # 2% tolerance
                )

            Composite uniqueness check::

                check = plugin.create_check(
                    columns=["category", "subcategory"],
                    allow_threshold=0.0
                )

        """
        if len(args) > 2 or set(kwargs) - {"columns", "allow_threshold"}:
            raise TypeError("create_check accepts columns and allow_threshold")
        columns = kwargs.get("columns", args[0] if args else None)
        allow_threshold = kwargs.get("allow_threshold", args[1] if len(args) > 1 else 0.0)
        if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
            raise TypeError("columns must be a list of strings")
        if not isinstance(allow_threshold, (int, float)):
            raise TypeError("allow_threshold must be numeric")

        from phlo_pandera.checks import UniqueCheck

        return UniqueCheck(columns=columns, allow_threshold=allow_threshold)
