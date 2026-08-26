"""Provider-neutral table naming and table-store helpers.

Parses catalog.namespace.table names, resolves the active TableStore from the
runtime, and routes ensure/exists/schema/stats/append/merge/overwrite calls
through one helper surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phlo.capabilities import resolve_capability, resolve_runtime_ref
from phlo.exceptions import PhloConfigError
from phlo.helpers._common import OperationSummary, coerce_int


@dataclass(frozen=True, slots=True)
class TableName:
    """Normalized table identifier."""

    table: str
    namespace: str | None = None
    catalog: str | None = None

    @property
    def qualified(self) -> str:
        """Return the most specific dotted table name."""
        return ".".join(part for part in (self.catalog, self.namespace, self.table) if part)

    @property
    def namespace_table(self) -> str:
        """Return namespace.table form when namespace exists."""
        return ".".join(part for part in (self.namespace, self.table) if part)


def parse_table_name(name: str, *, default_namespace: str | None = None) -> TableName:
    """Parse table, namespace.table, or catalog.namespace.table."""
    parts = [part for part in name.split(".") if part]
    if len(parts) == 1:
        return TableName(table=parts[0], namespace=default_namespace)
    if len(parts) == 2:
        return TableName(namespace=parts[0], table=parts[1])
    if len(parts) == 3:
        return TableName(catalog=parts[0], namespace=parts[1], table=parts[2])
    raise PhloConfigError(
        message=f"Table name must be table, namespace.table, or catalog.namespace.table: {name}",
        suggestions=["Use a table name such as raw.events or iceberg.raw.events."],
    )


SUPPORTED_DEDUPLICATION_METHODS = ("first", "last")


def deduplicate_arrow_by_unique_key(
    arrow_table,
    unique_key: str,
    *,
    method: str = "last",
    order_by: str | None = None,
):
    """Deduplicate rows sharing the same unique-key value within one batch.

    Applies deterministic, batch-local deduplication on a PyArrow Table so that
    delete-then-append merge paths never leave duplicate keys behind.

    Semantics:
        - ``method="last"`` keeps, per key, the row with the greatest
          ``order_by`` value. ``order_by`` must be provided; Parquet row order
          is never used as an implicit tiebreaker.
        - ``method="first"`` keeps the first occurrence of each key in input
          order.
        - Rows without duplicate keys are returned unchanged (identity on key).

    Args:
        arrow_table: PyArrow Table to deduplicate.
        unique_key: Column identifying distinct entities.
        method: Deduplication strategy, ``first`` or ``last``.
        order_by: Column defining recency/version ordering. Required for
            ``method="last"`` when duplicates are present.

    Returns:
        tuple: ``(deduplicated_table, duplicates_removed_count)``.

    Raises:
        ValueError: If the method is unsupported, or ``order_by`` is missing
            or absent from the data where required.

    """
    if method not in SUPPORTED_DEDUPLICATION_METHODS:
        raise ValueError(
            f"Unsupported deduplication_method '{method}'. "
            f"Supported methods: {', '.join(SUPPORTED_DEDUPLICATION_METHODS)}"
        )
    if order_by is not None and order_by not in arrow_table.schema.names:
        raise ValueError(
            f"Deduplication order column '{order_by}' not found in data. "
            f"Available columns: {list(arrow_table.schema.names)}"
        )

    key_values = arrow_table.column(unique_key).to_pylist()
    has_duplicates = len(set(key_values)) < len(key_values)
    ordered_indices = list(range(len(key_values)))
    if method == "last" and has_duplicates:
        if order_by is None:
            raise ValueError(
                "deduplication_method='last' requires an explicit ordering column "
                "(deduplication_order_by) because duplicate unique-key values were "
                "found; Parquet row order is not a valid tiebreaker"
            )
        # Stable Python sort keeps Parquet row order as a deterministic
        # tiebreaker between rows equal on the ordering column.
        order_values = arrow_table.column(order_by).to_pylist()
        ordered_indices = sorted(range(len(key_values)), key=lambda i: order_values[i])

    winners: dict[object, int] = {}
    for index in ordered_indices:
        key = key_values[index]
        if method == "last":
            winners[key] = index
        else:
            winners.setdefault(key, index)

    removed = len(key_values) - len(winners)
    if removed == 0:
        return arrow_table, 0

    kept_indices = sorted(winners.values())
    return arrow_table.take(kept_indices), removed


def qualified_table_name(
    table: str,
    *,
    namespace: str | None = None,
    catalog: str | None = None,
) -> str:
    """Build a qualified table name."""
    parsed = parse_table_name(table, default_namespace=namespace)
    return TableName(
        table=parsed.table,
        namespace=parsed.namespace,
        catalog=parsed.catalog or catalog,
    ).qualified


def resolve_table_store(name: str | None = None, *, runtime: Any = None) -> Any:
    """Resolve the active table-store provider or raise a guided error."""
    resolution = resolve_capability("table_store", name, runtime=runtime)
    if resolution is None:
        raise PhloConfigError(
            message="No table_store capability could be resolved",
            suggestions=[
                "Install a table-store package such as phlo-iceberg or phlo-delta.",
                "Configure PHLO_DEFAULT_CAPABILITIES=table_store:<name> when multiple providers exist.",
            ],
        )
    return resolution.provider


def ensure_lakehouse_table(
    table_name: str,
    *,
    schema: Any,
    partition_spec: Any = None,
    table_store: Any = None,
    runtime: Any = None,
    ref: str | None = None,
) -> Any:
    """Ensure a table exists through the active table-store capability."""
    provider = table_store or resolve_table_store(runtime=runtime)
    support = getattr(provider, "support", None)
    effective_ref = ref or resolve_runtime_ref(runtime, support=support)
    return provider.ensure_table(
        table_name=table_name,
        schema=schema,
        partition_spec=partition_spec,
        override_ref=effective_ref,
    )


def table_exists(table_name: str, *, table_store: Any = None, runtime: Any = None) -> bool:
    """Return whether a table exists using common provider methods."""
    provider = table_store or resolve_table_store(runtime=runtime)
    if hasattr(provider, "table_exists"):
        return bool(provider.table_exists(table_name=table_name))
    # Any inspection failure counts as "does not exist" instead of
    # propagating; absence and uninspectable states are not distinguished.
    try:
        if hasattr(provider, "get_catalog"):
            provider.get_catalog().load_table(table_name)
            return True
        if hasattr(provider, "get_table"):
            provider.get_table(table_name)
            return True
        if hasattr(provider, "load_table"):
            provider.load_table(table_name)
            return True
    except Exception:
        return False
    return False


def load_table_schema(table_name: str, *, table_store: Any = None, runtime: Any = None) -> Any:
    """Load a table schema using common provider methods."""
    provider = table_store or resolve_table_store(runtime=runtime)
    if hasattr(provider, "load_table_schema"):
        return provider.load_table_schema(table_name=table_name)
    table = None
    if hasattr(provider, "get_table"):
        table = provider.get_table(table_name)
    elif hasattr(provider, "get_catalog"):
        table = provider.get_catalog().load_table(table_name)
    if table is not None and hasattr(table, "schema"):
        schema = table.schema
        return schema() if callable(schema) else schema
    raise PhloConfigError(
        message=f"Provider cannot load schema for {table_name}",
        suggestions=["Use a table-store provider that exposes schema inspection."],
    )


def table_stats(table_name: str, *, table_store: Any = None, runtime: Any = None) -> dict[str, Any]:
    """Return table stats when the provider supports them."""
    provider = table_store or resolve_table_store(runtime=runtime)
    if hasattr(provider, "get_table_stats"):
        return dict(provider.get_table_stats(table_name=table_name))
    if hasattr(provider, "table_stats"):
        return dict(provider.table_stats(table_name=table_name))
    if hasattr(provider, "get_catalog"):
        table = provider.get_catalog().load_table(table_name)
        current_snapshot = table.current_snapshot() if hasattr(table, "current_snapshot") else None
        snapshots = list(table.snapshots()) if hasattr(table, "snapshots") else []
        return {
            "table_name": table_name,
            "snapshot_count": len(snapshots),
            "current_snapshot_id": getattr(current_snapshot, "snapshot_id", None),
        }
    raise PhloConfigError(
        message=f"Provider cannot return stats for {table_name}",
        suggestions=["Use a table-store provider with get_table_stats/table_stats support."],
    )


def empty_table(table_name: str, *, table_store: Any = None, runtime: Any = None) -> bool:
    """Return whether a table appears to contain no rows."""
    stats = table_stats(table_name, table_store=table_store, runtime=runtime)
    return coerce_int(stats.get("row_count") or stats.get("rows") or stats.get("num_records")) == 0


def append_parquet(
    table_name: str,
    data_path: str | Path,
    *,
    table_store: Any = None,
    runtime: Any = None,
    ref: str | None = None,
) -> OperationSummary:
    """Append a parquet batch through the active table store."""
    provider = table_store or resolve_table_store(runtime=runtime)
    support = getattr(provider, "support", None)
    effective_ref = ref or resolve_runtime_ref(runtime, support=support)
    result = provider.append_parquet(
        table_name=table_name,
        data_path=data_path,
        override_ref=effective_ref,
    )
    return OperationSummary(status="success", rows_inserted=coerce_int(result.get("rows_inserted")))


def merge_batch(
    table_name: str,
    data_path: str | Path,
    *,
    unique_key: str,
    table_store: Any = None,
    runtime: Any = None,
    ref: str | None = None,
) -> OperationSummary:
    """Merge a parquet batch through the active table store."""
    provider = table_store or resolve_table_store(runtime=runtime)
    support = getattr(provider, "support", None)
    effective_ref = ref or resolve_runtime_ref(runtime, support=support)
    result = provider.merge_parquet(
        table_name=table_name,
        data_path=data_path,
        unique_key=unique_key,
        override_ref=effective_ref,
    )
    return OperationSummary(
        status="success",
        rows_inserted=coerce_int(result.get("rows_inserted")),
        rows_deleted=coerce_int(result.get("rows_deleted")),
        rows_updated=coerce_int(result.get("rows_updated")),
    )


def overwrite_table(
    table_name: str,
    data_path: str | Path,
    *,
    table_store: Any = None,
    runtime: Any = None,
    ref: str | None = None,
) -> OperationSummary:
    """Overwrite a table with a parquet batch."""
    provider = table_store or resolve_table_store(runtime=runtime)
    support = getattr(provider, "support", None)
    effective_ref = ref or resolve_runtime_ref(runtime, support=support)
    result = provider.overwrite_parquet(
        table_name=table_name,
        data_path=data_path,
        override_ref=effective_ref,
    )
    return OperationSummary(status="success", rows_inserted=coerce_int(result.get("rows_inserted")))
