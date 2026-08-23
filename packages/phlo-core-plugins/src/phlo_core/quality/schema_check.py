"""Schema check plugin for validating data structure and types.

This module provides the SchemaCheckPlugin, which enables validation of
data against expected schemas including column presence and data type checks.
It integrates with Pandera to provide comprehensive schema validation
capabilities.

Example:
    Using the schema check plugin with a Pandera schema::

        import pandera as pa
        from phlo_core.quality.schema_check import SchemaCheckPlugin

        # Define expected schema
        schema = pa.DataFrameSchema({
            "id": pa.Column(pa.Int64, nullable=False),
            "name": pa.Column(pa.String, nullable=False),
            "email": pa.Column(pa.String, nullable=False),
            "created_at": pa.Column(pa.DateTime, nullable=False),
        })

        # Create the check
        plugin = SchemaCheckPlugin()
        check = plugin.create_check(schema=schema, lazy=True)

        # Apply to data
        validated_df = check.validate(dataframe)

"""

from typing import Any

from phlo.plugins import PluginMetadata, QualityCheckPlugin


class SchemaCheckPlugin(QualityCheckPlugin[Any]):
    """Plugin for performing schema validation on data.
    This plugin creates schema check instances that validate data against
    expected column structures and data types. It supports both strict
    validation (fails immediately) and lazy validation (collects all errors).

    The schema check is particularly useful for:
        - Validating column presence in incoming data
        - Ensuring correct data types before processing
        - Detecting schema drift in data pipelines
        - Enforcing contracts between data producers and consumers

    Example:
        Create and use a schema check::

            from phlo_core.quality.schema_check import SchemaCheckPlugin
            import pandera as pa

            plugin = SchemaCheckPlugin()
            schema = pa.DataFrameSchema({
                "user_id": pa.Column(pa.Int64),
                "username": pa.Column(pa.String)
            })

            check = plugin.create_check(schema=schema, lazy=True)
            result = check.validate(df)
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the schema-check plugin."""
        return PluginMetadata(
            name="schema_check",
            version="0.1.0",
            description="Schema validation for expected columns and types",
            author="Phlo Team",
            tags=["quality", "schema"],
        )

    def create_check(self, *args: Any, **kwargs: Any) -> Any:
        """Create a schema check instance.
        Creates and returns a configured SchemaCheck instance from phlo_pandera
        that validates data against the provided schema.

        Example:
            Create a schema check with lazy validation::

                from phlo_core.quality.schema_check import SchemaCheckPlugin
                import pandera as pa

                plugin = SchemaCheckPlugin()
                schema = pa.DataFrameSchema({
                    "id": pa.Column(pa.Int64, nullable=False),
                    "value": pa.Column(pa.Float, nullable=True)
                })

                check = plugin.create_check(schema=schema, lazy=True)

            Create a schema check with strict validation::

                strict_check = plugin.create_check(schema=schema, lazy=False)
        """
        if len(args) > 2 or set(kwargs) - {"schema", "lazy"}:
            raise TypeError("create_check accepts schema and lazy")
        schema = kwargs.get("schema", args[0] if args else None)
        lazy = kwargs.get("lazy", args[1] if len(args) > 1 else True)
        if schema is None or not isinstance(lazy, bool):
            raise TypeError("schema is required and lazy must be boolean")

        from phlo_pandera.checks_extra import SchemaCheck

        return SchemaCheck(schema=schema, lazy=lazy)
