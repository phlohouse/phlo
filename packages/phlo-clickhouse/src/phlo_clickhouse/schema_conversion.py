"""Pandera-to-ClickHouse schema conversion utilities.

Converts Pandera DataFrameModel schemas to pyarrow schemas - the canonical
schema form consumed by :mod:`phlo_clickhouse.resource` for DDL rendering and
parquet coercion. Type mapping, nullability, and reserved metadata columns
mirror ``phlo_iceberg.schema_conversion`` so both table stores derive
equivalent logical schemas from the same contract.

Supported type mappings:
    - str -> string
    - int -> int64
    - float -> float64
    - bool -> bool_
    - datetime -> timestamp("us", tz="UTC")
    - date -> date32
    - bytes -> binary
    - Decimal -> float64

The conversion automatically appends the standard metadata columns for DLT and
Phlo traceability: ``_dlt_load_id``, ``_dlt_id``, ``_phlo_ingested_at``,
``_phlo_row_id``, ``_phlo_partition_date``, and ``_phlo_run_id``.

Example:
    Convert a Pandera model to an arrow schema::

        from pandera.pandas import DataFrameModel, Column, Int64, String

        class UserSchema(DataFrameModel):
            id: Column[Int64]
            name: Column[String]

        import phlo_clickhouse.schema_conversion as sc
        schema = sc.pandera_to_arrow(UserSchema)

See Also:
    phlo_iceberg.schema_conversion for the Iceberg counterpart.
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
    """Raised when a Pandera schema cannot be converted for ClickHouse."""


_DLT_METADATA_FIELDS: tuple[tuple[str, pa.DataType], ...] = (
    ("_dlt_load_id", pa.string()),
    ("_dlt_id", pa.string()),
)

_PHLO_METADATA_FIELDS: tuple[tuple[str, pa.DataType], ...] = (
    ("_phlo_ingested_at", pa.timestamp("us", tz="UTC")),
    ("_phlo_row_id", pa.string()),
    ("_phlo_partition_date", pa.string()),
    ("_phlo_run_id", pa.string()),
)


def pandera_to_arrow(
    pandera_schema: type[DataFrameModel],
    add_dlt_metadata: bool = True,
    add_phlo_metadata: bool = True,
) -> pa.Schema:
    """Convert a Pandera DataFrameModel to an arrow schema.

    Maps column annotations through :func:`_map_type`, preserves nullability
    from the instantiated Pandera schema, and optionally appends the reserved
    DLT and Phlo metadata columns.
    """
    try:
        annotations = get_type_hints(pandera_schema)
    except Exception as exc:
        logger.exception(
            "clickhouse_schema_conversion_type_hints_failed",
            schema_name=pandera_schema.__name__,
        )
        raise SchemaConversionError(
            f"Failed to get type hints from Pandera schema {pandera_schema.__name__}: {exc}"
        ) from exc

    if not annotations:
        raise SchemaConversionError(
            f"Pandera schema {pandera_schema.__name__} has no field annotations"
        )

    try:
        pandera_schema_obj = pandera_schema.to_schema()
    except Exception as exc:
        raise SchemaConversionError(
            f"Failed to instantiate Pandera schema {pandera_schema.__name__}: {exc}"
        ) from exc

    fields: list[pa.Field] = []
    for field_name, annotation in annotations.items():
        if field_name.startswith("__") or field_name == "Config":
            continue

        nullable = True
        if field_name in pandera_schema_obj.columns:
            nullable = pandera_schema_obj.columns[field_name].nullable

        fields.append(pa.field(field_name, _map_type(field_name, annotation), nullable=nullable))

    if add_dlt_metadata:
        fields.extend(pa.field(name, dtype) for name, dtype in _DLT_METADATA_FIELDS)
    if add_phlo_metadata:
        fields.extend(pa.field(name, dtype) for name, dtype in _PHLO_METADATA_FIELDS)

    return pa.schema(fields)


def _map_type(field_name: str, pandera_type: Any) -> pa.DataType:
    """Map a Pandera-annotated type to an arrow type, unwrapping Optional.

    Lists, dicts, and bare ``Any`` are not representable in a typed ClickHouse
    table and raise :class:`SchemaConversionError`; store them as JSON strings
    instead.
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

    for arg in get_args(pandera_type):
        if arg is type(None):
            continue
        return _map_type(field_name, arg)

    return pa.string()


def _map_scalar(field_name: str, t: Any) -> pa.DataType:
    """Map a scalar Python type to its arrow equivalent."""
    scalar_map: dict[Any, pa.DataType] = {
        str: pa.string(),
        int: pa.int64(),
        float: pa.float64(),
        bool: pa.bool_(),
        datetime: pa.timestamp("us", tz="UTC"),
        date: pa.date32(),
        bytes: pa.binary(),
        Decimal: pa.float64(),
    }
    arrow_type = scalar_map.get(t)
    if arrow_type is None:
        raise SchemaConversionError(f"Unsupported type for field {field_name}: {t}")
    return arrow_type
