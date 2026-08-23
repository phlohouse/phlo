"""Normalized schema construction helpers.

Builds NormalizedSchema values from plain mappings, pandas-like frames, or
pyarrow schemas. Mapping form derives nullability from the required set;
dataframe inference is all-nullable and rejects non-dataframe input with a
PhloConfigError.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from phlo.capabilities import FieldSpec, NormalizedSchema
from phlo.exceptions import PhloConfigError


def normalized_schema(
    fields: Mapping[str, str] | list[FieldSpec],
    *,
    required: set[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> NormalizedSchema:
    """Build a NormalizedSchema from simple mappings or FieldSpec objects."""
    if isinstance(fields, Mapping):
        required = required or set()
        field_specs = [
            FieldSpec(name=str(name), dtype=str(dtype), nullable=str(name) not in required)
            for name, dtype in fields.items()
        ]
    else:
        field_specs = list(fields)
    return NormalizedSchema(fields=field_specs, metadata=metadata or {})


def schema_from_dataframe(df: Any) -> NormalizedSchema:
    """Infer a NormalizedSchema from a pandas-like DataFrame."""
    if not hasattr(df, "dtypes"):
        raise PhloConfigError(
            message="schema_from_dataframe expects a pandas-like DataFrame",
            suggestions=["Pass a pandas DataFrame or use schema_from_arrow for Arrow schemas."],
        )
    return NormalizedSchema(
        fields=[
            FieldSpec(name=str(name), dtype=str(dtype), nullable=True)
            for name, dtype in df.dtypes.items()
        ]
    )


def schema_from_arrow(schema: Any) -> NormalizedSchema:
    """Infer a NormalizedSchema from a pyarrow schema."""
    return NormalizedSchema(
        fields=[
            FieldSpec(name=field.name, dtype=str(field.type), nullable=field.nullable)
            for field in schema
        ]
    )


def schema_field_map(schema: NormalizedSchema) -> dict[str, FieldSpec]:
    """Return fields keyed by name."""
    return {field.name: field for field in schema.fields}
