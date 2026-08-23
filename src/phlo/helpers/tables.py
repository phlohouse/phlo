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
