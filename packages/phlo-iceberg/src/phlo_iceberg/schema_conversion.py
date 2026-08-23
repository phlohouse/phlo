"""Pandera-to-Iceberg schema conversion utilities.

This module provides utilities for converting Pandera DataFrameModel schemas
to PyIceberg Schema objects. It handles type mapping, metadata field injection,
and field ID assignment.

Supported type mappings:
    - str -> StringType
    - int -> LongType
    - float -> DoubleType
    - bool -> BooleanType
    - datetime -> TimestamptzType
    - date -> DateType
    - bytes -> BinaryType
    - Decimal -> DoubleType

The conversion automatically adds standard metadata columns for DLT and Phlo
traceability including ``_dlt_load_id``, ``_dlt_id``, ``_phlo_ingested_at``,
``_phlo_row_id``, ``_phlo_partition_date``, and ``_phlo_run_id``.

Example:
    Convert Pandera model to Iceberg schema::

        from pandera import DataFrameModel, Column, Int64, String, Bool
        from phlo_iceberg.schema_conversion import pandera_to_iceberg

        class UserSchema(DataFrameModel):
            id: Column[Int64]
            name: Column[String]
            active: Column[Bool] = Field(nullable=True)

        iceberg_schema = pandera_to_iceberg(UserSchema)

        # Use with table creation
        from phlo_iceberg import ensure_table
        table = ensure_table("raw.users", schema=iceberg_schema)

See Also:
    Pandera documentation: https://pandera.readthedocs.io/
    PyIceberg schema docs: https://py.iceberg.apache.org/

"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, get_args, get_origin, get_type_hints

from pandera.pandas import DataFrameModel
from phlo.logging import get_logger
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BinaryType,
    BooleanType,
    DateType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

logger = get_logger(__name__)


class SchemaConversionError(Exception):
    """Raised when a Pandera schema cannot be converted to an Iceberg schema.

    This exception indicates that the schema conversion failed due to:
    - Unsupported field types
    - Missing type annotations
    - Invalid Pandera schema structure
    - Type mapping failures

    Example:
        Handle conversion errors::

            from phlo_iceberg.schema_conversion import (
                pandera_to_iceberg, SchemaConversionError
            )

            try:
                schema = pandera_to_iceberg(MyComplexModel)
            except SchemaConversionError as e:
                print(f"Schema conversion failed: {e}")
                # Fall back to manual schema definition

    """


def pandera_to_iceberg(
    pandera_schema: type[DataFrameModel],
    start_field_id: int = 1,
    add_dlt_metadata: bool = True,
    add_phlo_metadata: bool = True,
) -> Schema:
    """Convert a Pandera DataFrameModel schema to a PyIceberg Schema.

    Maps Pandera column types to Iceberg types, preserving nullability and
    descriptions, assigning field IDs sequentially from start_field_id, and
    optionally injecting DLT (`_dlt_load_id`, `_dlt_id`) and Phlo
    (`_phlo_ingested_at`, `_phlo_row_id`, `_phlo_partition_date`,
    `_phlo_run_id`) metadata columns.

    Metadata columns use reserved field IDs 100-105.

    Raises: SchemaConversionError when conversion fails due to unsupported
    types, missing annotations, or invalid schema structure.

    Example:
        Basic conversion::

            from pandera import DataFrameModel, Column, Int64, String
            from phlo_iceberg.schema_conversion import pandera_to_iceberg

            class EventSchema(DataFrameModel):
                event_id: Column[Int64]
                event_type: Column[String]

            schema = pandera_to_iceberg(EventSchema)
            print(f"Schema has {len(schema.fields)} fields")

        Conversion without metadata::

            schema = pandera_to_iceberg(
                EventSchema,
                add_dlt_metadata=False,
                add_phlo_metadata=False
            )
            # Only has event_id and event_type fields
    """
    reserved_field_ids: dict[str, int] = {
        "_dlt_load_id": 100,
        "_dlt_id": 101,
        "_phlo_ingested_at": 102,
        "_phlo_row_id": 103,
        "_phlo_partition_date": 104,
        "_phlo_run_id": 105,
    }
    fields: list[NestedField] = []
    next_field_id = start_field_id
    user_field_count = 0
    logger.info(
        "iceberg_schema_conversion_started",
        schema_name=pandera_schema.__name__,
        start_field_id=start_field_id,
        add_dlt_metadata=add_dlt_metadata,
        add_phlo_metadata=add_phlo_metadata,
    )

    try:
        annotations = get_type_hints(pandera_schema)
    except Exception as e:
        logger.exception(
            "iceberg_schema_conversion_type_hints_failed",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Failed to get type hints from Pandera schema {pandera_schema.__name__}: {e}"
        ) from e

    if not annotations:
        logger.error(
            "iceberg_schema_conversion_no_annotations",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Pandera schema {pandera_schema.__name__} has no field annotations"
        )

    try:
        pandera_schema_obj = pandera_schema.to_schema()
    except Exception as e:
        logger.exception(
            "iceberg_schema_conversion_schema_build_failed",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Failed to instantiate Pandera schema {pandera_schema.__name__}: {e}"
        ) from e

    for field_name, field_type in annotations.items():
        if field_name.startswith("__") or field_name == "Config":
            continue
        user_field_count += 1

        description = ""
        nullable = True

        if field_name in pandera_schema_obj.columns:
            column = pandera_schema_obj.columns[field_name]
            nullable = column.nullable
            description = column.description or ""

        try:
            iceberg_type = _map_type(field_name, field_type)
        except SchemaConversionError as e:
            logger.warning(
                "iceberg_schema_conversion_field_type_unsupported",
                schema_name=pandera_schema.__name__,
                field_name=field_name,
            )
            raise SchemaConversionError(
                f"Cannot map Pandera type for field {field_name}: {e}"
            ) from e

        field_id = reserved_field_ids.get(field_name, next_field_id)
        if field_name not in reserved_field_ids:
            next_field_id += 1

        fields.append(
            NestedField(
                field_id=field_id,
                name=field_name,
                field_type=iceberg_type,
                required=not nullable,
                doc=description,
            )
        )

    if user_field_count == 0:
        logger.error(
            "iceberg_schema_conversion_no_fields",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(f"No fields found in Pandera schema {pandera_schema.__name__}")

    if add_dlt_metadata:
        existing_names = {f.name for f in fields}
        if "_dlt_load_id" not in existing_names:
            fields.append(
                NestedField(
                    field_id=100,
                    name="_dlt_load_id",
                    field_type=StringType(),
                    required=True,
                    doc="DLT load identifier",
                )
            )
        if "_dlt_id" not in existing_names:
            fields.append(
                NestedField(
                    field_id=101,
                    name="_dlt_id",
                    field_type=StringType(),
                    required=True,
                    doc="DLT record identifier",
                )
            )

    if add_phlo_metadata:
        existing_names = {f.name for f in fields}
        if "_phlo_row_id" not in existing_names:
            fields.append(
                NestedField(
                    field_id=103,
                    name="_phlo_row_id",
                    field_type=StringType(),
                    required=True,
                    doc="Phlo row-level lineage identifier (ULID)",
                )
            )
        if "_phlo_ingested_at" not in existing_names:
            fields.append(
                NestedField(
                    field_id=102,
                    name="_phlo_ingested_at",
                    field_type=TimestamptzType(),
                    required=True,
                    doc="UTC timestamp when phlo processed this record",
                )
            )
        if "_phlo_partition_date" not in existing_names:
            fields.append(
                NestedField(
                    field_id=104,
                    name="_phlo_partition_date",
                    field_type=StringType(),
                    required=True,
                    doc="Partition date used for ingestion (YYYY-MM-DD)",
                )
            )
        if "_phlo_run_id" not in existing_names:
            fields.append(
                NestedField(
                    field_id=105,
                    name="_phlo_run_id",
                    field_type=StringType(),
                    required=True,
                    doc="Dagster run ID for traceability",
                )
            )

    logger.info(
        "iceberg_schema_conversion_finished",
        schema_name=pandera_schema.__name__,
        total_field_count=len(fields),
        user_field_count=user_field_count,
    )
    return Schema(*fields)


def _map_type(field_name: str, pandera_type: Any) -> Any:
    """Map a Pandera-annotated type to an Iceberg type, handling Optional and
    generic types via scalar mappings.

    Lists and dictionaries are explicitly unsupported.

    Raises: SchemaConversionError when the type is a list, dict, or otherwise
    cannot be represented in Iceberg.

    Example:
        Mapping types::

            str_type = _map_type("name", str)  # Returns StringType()
            opt_int = _map_type("age", Optional[int])  # Returns LongType()

    Note:
        Complex nested types should be flattened or stored as JSON strings.
    """
    origin = get_origin(pandera_type)
    if origin is None:
        return _map_scalar(field_name, pandera_type)

    if origin is list:
        raise SchemaConversionError(f"Lists are not supported for field {field_name}")

    if origin is dict:
        raise SchemaConversionError(f"Dicts are not supported for field {field_name}")

    if origin is Any:
        return StringType()

    # Optional[T] / Union[T, None]
    if origin is type(None):
        return StringType()

    args = get_args(pandera_type)
    for arg in args:
        if arg is type(None):
            continue
        return _map_type(field_name, arg)

    return StringType()


def _map_scalar(field_name: str, t: Any) -> Any:
    """Map a scalar Python type (str, int, float, bool, datetime, date, bytes,
    Decimal) to its Iceberg equivalent.

    Raises: SchemaConversionError when the type is not supported.

    Example:
        Scalar mappings::

            assert isinstance(_map_scalar("id", int), LongType)
            assert isinstance(_map_scalar("name", str), StringType)
            assert isinstance(_map_scalar("score", float), DoubleType)
    """
    if t in (str,):
        return StringType()
    if t in (int,):
        return LongType()
    if t in (float,):
        return DoubleType()
    if t in (bool,):
        return BooleanType()
    if t in (datetime,):
        return TimestamptzType()
    if t in (date,):
        return DateType()
    if t in (bytes,):
        return BinaryType()
    if t in (Decimal,):
        return DoubleType()

    raise SchemaConversionError(f"Unsupported type for field {field_name}: {t}")
