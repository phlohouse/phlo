"""Nonmutating write preparation and explicit Arrow compatibility checks.

Schema additions are staged on the returned transaction, never committed here.
Callers can validate other write payloads before entering that transaction.
"""

from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.table import Table, Transaction

SchemaPolicy = Literal["strict", "additive", "drop_extra"]
SCHEMA_POLICIES = frozenset({"strict", "additive", "drop_extra"})


def validate_schema_policy(policy: str) -> None:
    """Reject unknown policies before catalog or data changes."""
    if policy not in SCHEMA_POLICIES:
        raise ValueError(f"Unknown schema policy {policy!r}; expected {sorted(SCHEMA_POLICIES)}")


def _compatible_type(source: pa.DataType, target: pa.DataType) -> bool:
    """Allow lossless representation changes, not value-dependent narrowing."""
    if source == target or pa.types.is_null(source):
        return True
    if pa.types.is_signed_integer(source) and pa.types.is_signed_integer(target):
        return source.bit_width <= target.bit_width
    if pa.types.is_floating(source) and pa.types.is_floating(target):
        return source.bit_width <= target.bit_width
    if pa.types.is_signed_integer(source) and pa.types.is_floating(target):
        # Arrow's safe cast checks integer values against the exact float range.
        return True
    if (pa.types.is_string(source) or pa.types.is_large_string(source)) and (
        pa.types.is_string(target) or pa.types.is_large_string(target)
    ):
        return True
    if (pa.types.is_binary(source) or pa.types.is_large_binary(source)) and (
        pa.types.is_binary(target) or pa.types.is_large_binary(target)
    ):
        return True
    if pa.types.is_struct(source) and pa.types.is_struct(target):
        return len(source) == len(target) and all(
            left.name == right.name and _compatible_type(left.type, right.type)
            for left, right in zip(source, target, strict=True)
        )
    if (pa.types.is_list(source) or pa.types.is_large_list(source)) and (
        pa.types.is_list(target) or pa.types.is_large_list(target)
    ):
        return _compatible_type(source.value_type, target.value_type)
    if pa.types.is_map(source) and pa.types.is_map(target):
        return _compatible_type(source.key_type, target.key_type) and _compatible_type(
            source.item_type, target.item_type
        )
    if pa.types.is_timestamp(source) and pa.types.is_timestamp(target):
        utc = {"UTC", "+00:00", "Etc/UTC", "Z"}
        return source.tz == target.tz or (source.tz in utc and target.tz in utc)
    if pa.types.is_decimal(source) and pa.types.is_decimal(target):
        return (
            source.scale <= target.scale
            and source.precision - source.scale <= target.precision - target.scale
        )
    return False


def _validate_required(array: pa.Array, field: pa.Field, path: str) -> None:
    if not field.nullable and array.null_count:
        raise ValueError(f"Required target column '{path}' contains nulls")
    if pa.types.is_null(array.type):
        return
    if pa.types.is_struct(field.type):
        present = array.filter(array.is_valid())
        for index, child in enumerate(field.type):
            _validate_required(present.field(index), child, f"{path}.{child.name}")
    elif pa.types.is_list(field.type) or pa.types.is_large_list(field.type):
        _validate_required(
            pc.call_function("list_flatten", [array]), field.type.value_field, f"{path}[]"
        )
    elif pa.types.is_map(field.type):
        present = array.filter(array.is_valid())
        _validate_required(present.keys, field.type.key_field, f"{path}.key")
        _validate_required(present.items, field.type.item_field, f"{path}.value")


def _align_arrow_table_to_target_schema(
    arrow_table: pa.Table, target_schema: pa.Schema, *, table_name: str
) -> pa.Table:
    """Order target columns, fill optional fields and reject lossy writes."""
    if len(set(arrow_table.column_names)) != len(arrow_table.column_names):
        raise ValueError(f"Duplicate source column names for {table_name}")
    columns = []
    for field in target_schema:
        if field.name not in arrow_table.column_names:
            if not field.nullable:
                raise ValueError(
                    f"Required target column '{field.name}' is missing from source data for {table_name}"
                )
            columns.append(pa.nulls(len(arrow_table), type=field.type))
            continue
        column = arrow_table[field.name]
        if not _compatible_type(column.type, field.type):
            raise ValueError(
                f"Incompatible type for {table_name}.{field.name}: {column.type} -> {field.type}; "
                "use an explicit schema migration"
            )
        for chunk in column.chunks:
            _validate_required(chunk, field, field.name)
        try:
            column = column.cast(field.type, safe=True)
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
            raise ValueError(f"Unsafe cast for {table_name}.{field.name}: {exc}") from exc
        columns.append(column)
    return pa.Table.from_arrays(columns, schema=target_schema)


def prepare_arrow_write(
    table: Table, arrow_table: pa.Table, *, table_name: str, schema_policy: SchemaPolicy
) -> tuple[Transaction, pa.Table, pa.Schema]:
    """Stage nullable additions and align data without publishing any changes."""
    validate_schema_policy(schema_policy)
    target = schema_to_pyarrow(table.schema())
    extras = [field for field in arrow_table.schema if field.name not in target.names]
    if extras and schema_policy == "strict":
        raise ValueError(
            f"Unexpected columns for {table_name}: {[field.name for field in extras]}; "
            "use schema_policy='additive' or explicitly opt into 'drop_extra'"
        )
    transaction = table.transaction()
    additions = pa.schema([])
    if extras and schema_policy == "additive":
        if any(not field.nullable for field in extras):
            raise ValueError(f"Additive policy only permits new nullable columns for {table_name}")
        with transaction.update_schema() as update:
            # Only missing fields are passed: union_by_name must not widen or relax existing fields.
            update.union_by_name(
                pa.schema(extras), format_version=transaction.table_metadata.format_version
            )
        target = schema_to_pyarrow(transaction.table_metadata.schema())
        additions = pa.schema([target.field(field.name) for field in extras])
    aligned = _align_arrow_table_to_target_schema(arrow_table, target, table_name=table_name)
    return transaction, aligned, additions


def validate_declared_schema(
    table: Table, desired: pa.Schema, *, table_name: str, schema_policy: SchemaPolicy
) -> None:
    """Check an existing table declaration without evolving it before batch validation."""
    validate_schema_policy(schema_policy)
    target = schema_to_pyarrow(table.schema())
    for field in desired:
        if field.name not in target.names:
            if schema_policy == "strict":
                raise ValueError(f"Unexpected column {table_name}.{field.name}")
            if schema_policy == "additive" and not field.nullable:
                raise ValueError(f"Additive policy only permits new nullable columns: {field.name}")
            continue
        existing = target.field(field.name)
        if field.type != existing.type or field.nullable != existing.nullable:
            raise ValueError(
                f"Schema change for {table_name}.{field.name} requires an explicit migration"
            )
