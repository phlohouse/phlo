"""Pandera-to-Delta (PyArrow) schema conversion utilities.

This module provides functions to convert Pandera DataFrameModel schemas
to PyArrow schemas suitable for Delta Lake table creation. It handles
type mapping, metadata column injection, and validation.

Example:
    from phlo_delta.schema_conversion import pandera_to_delta
    from my_schemas import EventSchema

    arrow_schema = pandera_to_delta(EventSchema)

"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, get_args, get_origin, get_type_hints

import pyarrow as pa
from pandera.pandas import DataFrameModel

from phlo.logging import get_logger

logger = get_logger(__name__)


class SchemaConversionError(Exception):
    """Raised when a Pandera schema cannot be converted to a Delta-compatible Arrow schema.

    This exception indicates that the source Pandera schema contains unsupported
    types, invalid annotations, or other conversion-blocking issues.

    Example:
        try:
            schema = pandera_to_delta(InvalidSchema)
        except SchemaConversionError as e:
            print(f"Conversion failed: {e}")

    """

    pass


def pandera_to_delta(
    pandera_schema: type[DataFrameModel],
    add_dlt_metadata: bool = True,
    add_phlo_metadata: bool = True,
) -> pa.Schema:
    """Convert a Pandera DataFrameModel schema to a PyArrow schema for Delta Lake.

    Transforms Pandera field annotations and constraints into equivalent PyArrow
    types. Optionally injects DLT and Phlo metadata columns. ``pandera_schema`` is
    the source Pandera model class with field annotations, ``add_dlt_metadata``
    controls appending standard DLT metadata columns (_dlt_load_id, _dlt_id), and
    ``add_phlo_metadata`` controls appending standard Phlo metadata columns
    (_phlo_row_id, _phlo_ingested_at, _phlo_partition_date, _phlo_run_id). Returns
    an equivalent PyArrow schema ready for Delta Lake table creation. Raises
    SchemaConversionError when conversion fails due to missing annotations, type
    mapping failures, or invalid schema structure.

    Example:
        from pandera.pandas import DataFrameModel
        from typing import Annotated
        import pandera as pa

        class EventSchema(DataFrameModel):
            event_id: Annotated[str, pa.Field(nullable=False)]
            timestamp: Annotated[datetime, pa.Field(nullable=False)]

        arrow_schema = pandera_to_delta(EventSchema)
    """
    fields: list[pa.Field] = []
    user_field_count = 0
    logger.info(
        "delta_schema_conversion_started",
        schema_name=pandera_schema.__name__,
        add_dlt_metadata=add_dlt_metadata,
        add_phlo_metadata=add_phlo_metadata,
    )

    try:
        annotations = get_type_hints(pandera_schema)
    except Exception as e:
        logger.exception(
            "delta_schema_conversion_type_hints_failed",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Failed to get type hints from Pandera schema {pandera_schema.__name__}: {e}"
        ) from e

    if not annotations:
        logger.error(
            "delta_schema_conversion_no_annotations",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Pandera schema {pandera_schema.__name__} has no field annotations"
        )

    try:
        pandera_schema_obj = pandera_schema.to_schema()
    except Exception as e:
        logger.exception(
            "delta_schema_conversion_schema_build_failed",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Failed to instantiate Pandera schema {pandera_schema.__name__}: {e}"
        ) from e

    for field_name, field_type in annotations.items():
        if field_name.startswith("__") or field_name == "Config":
            continue
        user_field_count += 1

        nullable = True
        if field_name in pandera_schema_obj.columns:
            column = pandera_schema_obj.columns[field_name]
            nullable = column.nullable

        try:
            arrow_type = _map_type(field_name, field_type)
        except SchemaConversionError as e:
            logger.warning(
                "delta_schema_conversion_field_type_unsupported",
                schema_name=pandera_schema.__name__,
                field_name=field_name,
            )
            raise SchemaConversionError(
                f"Cannot map Pandera type for field {field_name}: {e}"
            ) from e

        fields.append(pa.field(field_name, arrow_type, nullable=nullable))

    if user_field_count == 0:
        logger.error(
            "delta_schema_conversion_no_fields",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(f"No fields found in Pandera schema {pandera_schema.__name__}")

    if add_dlt_metadata:
        existing_names = {f.name for f in fields}
        if "_dlt_load_id" not in existing_names:
            fields.append(pa.field("_dlt_load_id", pa.string(), nullable=False))
        if "_dlt_id" not in existing_names:
            fields.append(pa.field("_dlt_id", pa.string(), nullable=False))

    if add_phlo_metadata:
        existing_names = {f.name for f in fields}
        if "_phlo_row_id" not in existing_names:
            fields.append(pa.field("_phlo_row_id", pa.string(), nullable=False))
        if "_phlo_ingested_at" not in existing_names:
            fields.append(
                pa.field("_phlo_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False)
            )
        if "_phlo_partition_date" not in existing_names:
            fields.append(pa.field("_phlo_partition_date", pa.string(), nullable=False))
        if "_phlo_run_id" not in existing_names:
            fields.append(pa.field("_phlo_run_id", pa.string(), nullable=False))

    logger.info(
        "delta_schema_conversion_finished",
        schema_name=pandera_schema.__name__,
        total_field_count=len(fields),
        user_field_count=user_field_count,
    )
    return pa.schema(fields)


def _map_type(field_name: str, pandera_type: Any) -> pa.DataType:
    """Map a Pandera-annotated type to a PyArrow type.

    Handles complex types (Optional, List, Dict) and delegates scalar types to
    _map_scalar. Rejects unsupported container types. ``field_name`` is the source
    field name for error reporting and ``pandera_type`` is the annotated
    Python/Pandera type. Returns the corresponding PyArrow data type. Raises
    SchemaConversionError when the type cannot be represented in PyArrow/Delta.

    Example:
        arrow_type = _map_type("user_id", str)
        # Returns: pa.string()
    """
    origin = get_origin(pandera_type)
    if origin is None:
        return _map_scalar(field_name, pandera_type)

    if origin is list:
        raise SchemaConversionError(f"Lists are not supported for field {field_name}")

    if origin is dict:
        raise SchemaConversionError(f"Dicts are not supported for field {field_name}")

    if origin is Any:
        return pa.string()

    if origin is type(None):
        return pa.string()

    args = get_args(pandera_type)
    for arg in args:
        if arg is type(None):
            continue
        return _map_type(field_name, arg)

    return pa.string()


def _map_scalar(field_name: str, t: Any) -> pa.DataType:
    """Map a scalar Python type to a PyArrow type.

    Converts basic Python types to their PyArrow equivalents for Delta Lake
    storage. ``field_name`` is the source field name for error reporting and
    ``t`` is the scalar Python type. Returns the corresponding PyArrow type.
    Raises SchemaConversionError when the type is unsupported.

    Example:
        arrow_type = _map_scalar("price", float)
        # Returns: pa.float64()
    """
    if t in (str,):
        return pa.string()
    if t in (int,):
        return pa.int64()
    if t in (float,):
        return pa.float64()
    if t in (bool,):
        return pa.bool_()
    if t in (datetime,):
        return pa.timestamp("us", tz="UTC")
    if t in (date,):
        return pa.date32()
    if t in (bytes,):
        return pa.binary()
    if t in (Decimal,):
        # Stored as float64 instead of a fixed-precision Arrow decimal; exact
        # decimal precision is not preserved through this conversion.
        return pa.float64()

    raise SchemaConversionError(f"Unsupported type for field {field_name}: {t}")
