"""Pandera SchemaExtractor implementation.

This module provides the PanderaSchemaExtractor class which converts Pandera
DataFrameModel subclasses into provider-agnostic NormalizedSchema objects.
These normalized schemas can be used by storage providers (Iceberg, Delta, etc.)
and schema migration tooling.

The extractor handles:
- Python type to storage type mapping
- Optional type unwrapping (Optional[T] -> T)
- Nullability detection from Pandera schema metadata
- Support for common Python types used in data engineering

Type Mapping:
    The extractor maps Python types to canonical storage types:
    - str -> "string"
    - int -> "int64"
    - float -> "float64"
    - bool -> "bool"
    - datetime -> "timestamptz"
    - date -> "date"
    - bytes -> "binary"
    - Decimal -> "float64"

Example:
    ```python
    from pandera.pandas import DataFrameModel, Field
    from phlo_pandera.schema_extractor import PanderaSchemaExtractor

    class CustomerSchema(DataFrameModel):
        customer_id: int = Field(gt=0)
        email: str | None = Field(nullable=True)
        created_at: datetime

    extractor = PanderaSchemaExtractor()
    normalized = extractor.extract(CustomerSchema)

    # normalized.fields contains:
    # - FieldSpec(name="customer_id", dtype="int64", nullable=False)
    # - FieldSpec(name="email", dtype="string", nullable=True)
    # - FieldSpec(name="created_at", dtype="timestamptz", nullable=True)
    ```

See Also:
    - ``schemas/base.py``: PhloSchema base class
    - ``schemas/asset_outputs.py``: Output schema definitions

"""

from __future__ import annotations

import types
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Union, get_args, get_origin, get_type_hints

from pandera.pandas import DataFrameModel

from phlo.capabilities.specs import FieldSpec, NormalizedSchema

_DTYPE_MAP: dict[type, str] = {
    str: "string",
    int: "int64",
    float: "float64",
    bool: "bool",
    datetime: "timestamptz",
    date: "date",
    bytes: "binary",
    Decimal: "float64",
}


def _map_dtype(python_type: type) -> str:
    """Map a scalar Python type to a canonical dtype string.

    Raise ValueError when the type has no known mapping.

    Example:
        ```python
        _map_dtype(str)    # Returns: "string"
        _map_dtype(int)    # Returns: "int64"
        _map_dtype(float)  # Returns: "float64"
        ```
    """
    dtype = _DTYPE_MAP.get(python_type)
    if dtype is None:
        raise ValueError(f"Unsupported type: {python_type}")
    return dtype


def _unwrap_optional(tp: Any) -> type:
    """Unwrap Optional[T] / Union[T, None] to the inner type T.

    Example:
        ```python
        _unwrap_optional(Optional[str])  # Returns: str
        _unwrap_optional(str | None)   # Returns: str
        _unwrap_optional(int)          # Returns: int
        ```
    """
    origin = get_origin(tp)
    if origin is Union or isinstance(tp, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _unwrap_pandera_series(tp: Any) -> Any:
    """Unwrap Pandera Series[T] annotations to their scalar type."""
    origin = get_origin(tp)
    if origin is None:
        return tp

    origin_module = getattr(origin, "__module__", "")
    origin_name = getattr(origin, "__name__", "")
    if origin_name == "Series" or origin_module.startswith("pandera.typing"):
        args = get_args(tp)
        if args:
            return args[0]

    return tp


def _unwrap_field_type(tp: Any) -> type:
    """Unwrap Optional and Pandera Series annotations before dtype mapping."""
    return _unwrap_optional(_unwrap_pandera_series(_unwrap_optional(tp)))


class PanderaSchemaExtractor:
    """Extract a NormalizedSchema from a Pandera DataFrameModel subclass.

    This class converts Pandera schema definitions into a provider-agnostic
    normalized format suitable for storage provider integration and schema
    migration tools.

    The extractor processes:
    - Type annotations (with Optional unwrapping)
    - Nullability metadata from Pandera columns
    - Field names and ordering

    Example:
        ```python
        from pandera.pandas import DataFrameModel, Field

        class OrderSchema(DataFrameModel):
            order_id: int = Field(unique=True)
            customer_id: int
            total: float = Field(ge=0)
            notes: str | None = Field(nullable=True)

        extractor = PanderaSchemaExtractor()
        schema = extractor.extract(OrderSchema)

        for field in schema.fields:
            print(f"{field.name}: {field.dtype} (nullable={field.nullable})")
        # Output:
        # order_id: int64 (nullable=False)
        # customer_id: int64 (nullable=True)
        # total: float64 (nullable=True)
        # notes: string (nullable=True)
        ```

    """

    def extract(self, native_schema: type[DataFrameModel]) -> NormalizedSchema:
        """Convert a Pandera DataFrameModel class into a NormalizedSchema.

        Produces one FieldSpec per annotated column from class annotations and
        Pandera column metadata. Raise ValueError when a type cannot be mapped to a
        canonical dtype.

        Example:
            ```python
            from phlo_pandera.schemas import PhloSchema

            class MySchema(PhloSchema):
                id: int
                name: str

            extractor = PanderaSchemaExtractor()
            normalized = extractor.extract(MySchema)
            ```
        """
        annotations = get_type_hints(native_schema)
        schema_obj = native_schema.to_schema()
        columns = schema_obj.columns

        fields: list[FieldSpec] = []
        for name, annotation in annotations.items():
            if name.startswith("__") or name == "Config":
                continue

            inner_type = _unwrap_field_type(annotation)
            dtype = _map_dtype(inner_type)

            nullable = True
            if name in columns:
                nullable = columns[name].nullable

            fields.append(FieldSpec(name=name, dtype=dtype, nullable=nullable))

        return NormalizedSchema(fields=fields)
