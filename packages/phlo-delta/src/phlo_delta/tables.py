"""Delta Lake table management utilities for creating, modifying, and querying tables.

This module provides standalone functions for Delta table operations using the
deltalake library. It handles table lifecycle operations (create, read, update),
data ingestion from parquet files, maintenance operations (vacuum, optimize),
and version management (time travel, rollback).

All functions operate on fully qualified table names in the format
"namespace.table" and use S3-compatible storage backends.

Example:
    from phlo_delta.tables import ensure_table, append_to_table, get_table_stats
    import pyarrow as pa

    schema = pa.schema([("id", pa.string()), ("value", pa.int64())])
    table = ensure_table("raw.events", schema)

    stats = get_table_stats("raw.events")
    print(f"Table has {stats['file_count']} files")

    result = append_to_table("raw.events", "/data/events.parquet")
    print(f"Inserted {result['rows_inserted']} rows")

"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from phlo.logging import get_logger
from phlo_delta.settings import get_settings

logger = get_logger(__name__)

DeltaTable = Any
# Type alias for DeltaTable from deltalake package (lazy-loaded).


def _load_deltalake() -> tuple[type[Any], Any]:
    """Load optional deltalake runtime symbols on demand.

    Lazily imports deltalake to avoid import-time dependencies and returns
    the (DeltaTable class, write_deltalake function) pair.

    Example:
        DeltaTable, write_deltalake = _load_deltalake()
        dt = DeltaTable(table_uri, storage_options=opts)

    """
    deltalake = cast(Any, importlib.import_module("deltalake"))
    return deltalake.DeltaTable, deltalake.write_deltalake


def _resolve_table_uri(table_name: str) -> str:
    """Build a full S3 URI for a Delta table from namespace.table format.

    Combines the configured warehouse path with the namespace and table name.
    Raises ValueError when table_name is not in namespace.table format.

    Example:
        uri = _resolve_table_uri("raw.events")
        # Returns: "s3://lake/warehouse/delta/raw/events"

    """
    parts = table_name.split(".")
    if len(parts) != 2:
        raise ValueError(f"Table name must be namespace.table, got: {table_name}")
    namespace, table = parts
    settings = get_settings()
    return f"{settings.delta_warehouse_path}/{namespace}/{table}"


def _default_storage_options(
    storage_options: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return storage options, falling back to settings if not provided.

    Uses the provided S3 options verbatim; otherwise returns the settings
    defaults (AWS credentials, endpoint URL, and other S3 configuration).

    Example:
        opts = _default_storage_options()  # Uses settings
        opts = _default_storage_options({"AWS_REGION": "eu-west-1"})  # Override

    """
    if storage_options is not None:
        return storage_options
    return get_settings().get_storage_options()


def _read_parquet(data_path: str | Path) -> pa.Table:
    """Read parquet data from a file or directory into a PyArrow Table.

    Supports both single parquet files and directories of parquet files.
    Raises whatever error the parquet reader produces when reading fails.

    Example:
        table = _read_parquet("/data/events.parquet")
        table = _read_parquet("/data/events/")  # Directory of parquet files

    """
    data_path = Path(data_path) if isinstance(data_path, str) else data_path
    if data_path.is_dir():
        return pq.ParquetDataset(str(data_path)).read()
    return pq.read_table(str(data_path))


def ensure_table(
    table_name: str,
    schema: pa.Schema,
    partition_columns: list[str] | None = None,
    storage_options: dict[str, str] | None = None,
) -> DeltaTable:
    """Ensure a Delta table exists, creating it if necessary.

    Returns the existing table, or creates a new empty table with the given
    schema and optional partition columns. The create path uses mode="error",
    so if another process creates the same table concurrently, this call
    fails instead of committing a second, possibly divergent initial version.
    Raises an exception if loading or creating the table fails.

    Example:
        schema = pa.schema([("id", pa.string()), ("value", pa.int64())])
        table = ensure_table("raw.events", schema, partition_columns=["date"])

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)
    delta_table_cls, write_deltalake = _load_deltalake()

    # Open before creating: any load failure is treated as "table missing". The
    # create path then uses mode="error", so if another process creates the same
    # table concurrently, this call fails instead of committing a second,
    # possibly divergent initial version.
    try:
        dt = delta_table_cls(table_uri, storage_options=opts)
        logger.info(
            "delta_table_loaded",
            table_name=table_name,
            table_uri=table_uri,
        )
        return dt
    except Exception:
        pass
    logger.info(
        "delta_table_creating",
        table_name=table_name,
        table_uri=table_uri,
    )

    empty_table = pa.table(
        {field.name: pa.array([], type=field.type) for field in schema},
        schema=schema,
    )
    write_deltalake(
        table_uri,
        empty_table,
        mode="error",
        partition_by=partition_columns,
        storage_options=opts,
    )

    dt = delta_table_cls(table_uri, storage_options=opts)
    logger.info(
        "delta_table_created",
        table_name=table_name,
        table_uri=table_uri,
    )
    return dt


def append_to_table(
    table_name: str,
    data_path: str | Path,
    storage_options: dict[str, str] | None = None,
) -> dict[str, int]:
    """Append parquet data to a Delta table.

    Reads parquet from data_path and appends it to the existing table,
    creating the table if it does not exist. Returns write statistics with
    rows_inserted (rows_deleted is always 0 for append). Raises an exception
    if reading parquet or writing to Delta fails.

    Example:
        result = append_to_table("raw.events", "/data/new_events.parquet")
        print(f"Inserted {result['rows_inserted']} rows")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)
    _delta_table_cls, write_deltalake = _load_deltalake()
    source_path = str(data_path)
    source_row_count = 0
    rows_inserted = 0

    logger.info(
        "delta_table_append_started",
        table_name=table_name,
        source=source_path,
    )

    try:
        arrow_table = _read_parquet(data_path)
        source_row_count = len(arrow_table)

        write_deltalake(
            table_uri,
            arrow_table,
            mode="append",
            storage_options=opts,
        )
        rows_inserted = source_row_count
    except Exception as exc:
        logger.error(
            "delta_table_append_failed",
            table_name=table_name,
            source=source_path,
            source_row_count=source_row_count,
            rows_inserted=rows_inserted,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    result = {"rows_inserted": rows_inserted, "rows_deleted": 0}
    logger.info(
        "delta_table_append_succeeded",
        table_name=table_name,
        source=source_path,
        source_row_count=source_row_count,
        rows_inserted=result["rows_inserted"],
    )
    return result


def merge_to_table(
    table_name: str,
    data_path: str | Path,
    unique_key: str,
    storage_options: dict[str, str] | None = None,
) -> dict[str, int]:
    """Merge (upsert) parquet data into a Delta table by unique_key.

    Updates existing rows matching unique_key and inserts rows not present
    in the target. Rows absent from the source are swallowed, not deleted,
    so rows_deleted reports Delta's counter and stays 0 under this strategy.
    Returns rows_inserted, rows_updated, and rows_deleted counts. Raises
    ValueError when unique_key is missing from the data columns, or an
    exception if the merge operation fails.

    Example:
        result = merge_to_table(
            "raw.events",
            "/data/events.parquet",
            unique_key="event_id"
        )
        print(f"Inserted: {result['rows_inserted']}, Updated: {result['rows_updated']}")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)
    source_path = str(data_path)
    source_row_count = 0

    logger.info(
        "delta_table_merge_started",
        table_name=table_name,
        source=source_path,
        unique_key=unique_key,
    )
    delta_table_cls, _write_deltalake = _load_deltalake()

    try:
        arrow_table = _read_parquet(data_path)
        source_row_count = len(arrow_table)

        if unique_key not in arrow_table.schema.names:
            raise ValueError(
                f"Unique key '{unique_key}' not found in data. "
                f"Available columns: {arrow_table.schema.names}"
            )

        dt = delta_table_cls(table_uri, storage_options=opts)
        merge_result = (
            dt.merge(
                source=arrow_table,
                predicate=f"target.{unique_key} = source.{unique_key}",
                source_alias="source",
                target_alias="target",
            )
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute()
        )

        rows_updated = merge_result.get("num_target_rows_updated", 0)
        rows_inserted = merge_result.get("num_target_rows_inserted", 0)
        rows_deleted = merge_result.get("num_target_rows_deleted", 0)
    except Exception as exc:
        logger.error(
            "delta_table_merge_failed",
            table_name=table_name,
            source=source_path,
            source_row_count=source_row_count,
            unique_key=unique_key,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    result = {
        "rows_inserted": rows_inserted,
        "rows_updated": rows_updated,
        "rows_deleted": rows_deleted,
    }
    logger.info(
        "delta_table_merge_succeeded",
        table_name=table_name,
        source=source_path,
        source_row_count=source_row_count,
        unique_key=unique_key,
        rows_inserted=result["rows_inserted"],
        rows_updated=result["rows_updated"],
        rows_deleted=result["rows_deleted"],
    )
    return result


def overwrite_table(
    table_name: str,
    data_path: str | Path,
    storage_options: dict[str, str] | None = None,
) -> dict[str, int]:
    """Overwrite a Delta table with parquet data.

    Replaces all existing data with the new parquet data; the old data
    remains accessible via time travel. Returns write statistics with
    rows_inserted (rows_deleted is always 0 for overwrite). Raises an
    exception if reading parquet or writing to Delta fails.

    Example:
        result = overwrite_table("raw.events", "/data/full_refresh.parquet")
        print(f"Overwrote with {result['rows_inserted']} rows")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)
    _delta_table_cls, write_deltalake = _load_deltalake()
    source_path = str(data_path)
    source_row_count = 0
    rows_inserted = 0

    logger.info(
        "delta_table_overwrite_started",
        table_name=table_name,
        source=source_path,
    )

    try:
        arrow_table = _read_parquet(data_path)
        source_row_count = len(arrow_table)

        write_deltalake(
            table_uri,
            arrow_table,
            mode="overwrite",
            storage_options=opts,
        )
        rows_inserted = source_row_count
    except Exception as exc:
        logger.error(
            "delta_table_overwrite_failed",
            table_name=table_name,
            source=source_path,
            source_row_count=source_row_count,
            rows_inserted=rows_inserted,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    result = {"rows_inserted": rows_inserted, "rows_deleted": 0}
    logger.info(
        "delta_table_overwrite_succeeded",
        table_name=table_name,
        source=source_path,
        source_row_count=source_row_count,
        rows_inserted=result["rows_inserted"],
    )
    return result


def delete_rows_from_table(
    table_name: str,
    predicate: str,
    storage_options: dict[str, str] | None = None,
) -> dict[str, int]:
    """Delete rows matching a predicate expression from a Delta table.

    Atomically removes rows matching the SQL predicate and creates a new
    table version. Returns {"rows_deleted": -1} because Delta does not
    return a count from predicate deletes. Raises an exception if the
    delete operation fails.

    Example:
        result = delete_rows_from_table(
            "raw.events",
            predicate="created_at < '2024-01-01'"
        )

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)

    logger.info(
        "delta_table_delete_started",
        table_name=table_name,
        predicate=predicate,
    )
    delta_table_cls, _write_deltalake = _load_deltalake()

    try:
        dt = delta_table_cls(table_uri, storage_options=opts)
        dt.delete(predicate)
    except Exception as exc:
        logger.error(
            "delta_table_delete_failed",
            table_name=table_name,
            predicate=predicate,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    result = {"rows_deleted": -1}
    logger.info(
        "delta_table_delete_succeeded",
        table_name=table_name,
        predicate=predicate,
    )
    return result


def expire_snapshots(
    table_name: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    """No-op for Delta Lake — snapshot expiration is handled by vacuum.

    Delta Lake does not support explicit snapshot expiration; old versions
    are managed by vacuum. Returns an info dict with deleted_snapshots=0
    and an explanatory note.

    Example:
        result = expire_snapshots("raw.events")
        print(result["note"])  # "Delta Lake does not support explicit snapshot expiration..."

    """
    logger.info(
        "delta_expire_snapshots_noop",
        table_name=table_name,
    )
    return {
        "deleted_snapshots": 0,
        "note": "Delta Lake does not support explicit snapshot expiration; use vacuum instead.",
    }


def remove_orphan_files(
    table_name: str,
    retain_hours: int = 168,
    storage_options: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Remove old files using Delta vacuum.

    Deletes data files no longer referenced by the table and older than
    retain_hours (default 168 = 7 days); enforce_retention_duration is
    disabled so the requested window is honored exactly. Returns a dict
    with files_removed count and removed_files paths capped at 100 entries.
    Raises an exception if the vacuum operation fails.

    Example:
        result = remove_orphan_files("raw.events", retain_hours=72)
        print(f"Removed {result['files_removed']} old files")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)

    logger.info(
        "delta_vacuum_started",
        table_name=table_name,
        retain_hours=retain_hours,
    )
    delta_table_cls, _write_deltalake = _load_deltalake()

    try:
        dt = delta_table_cls(table_uri, storage_options=opts)
        # Bypasses Delta's built-in 7-day minimum retention so the caller's
        # retain_hours is honored exactly; without this flag shorter windows
        # are silently widened to the default.
        removed = dt.vacuum(retention_hours=retain_hours, enforce_retention_duration=False)
    except Exception as exc:
        logger.error(
            "delta_vacuum_failed",
            table_name=table_name,
            retain_hours=retain_hours,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "delta_vacuum_succeeded",
        table_name=table_name,
        files_removed=len(removed),
    )
    return {
        "files_removed": len(removed),
        "removed_files": removed[:100],
    }


def get_table_stats(
    table_name: str,
    storage_options: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Get statistics about a Delta table.

    Retrieves metadata including table_name, version, file_count,
    total_size_bytes, total_size_mb, table_uri, description, and
    partition_columns. Raises an exception if the table cannot be accessed.

    Example:
        stats = get_table_stats("raw.events")
        print(f"Table v{stats['version']} has {stats['file_count']} files")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)
    delta_table_cls, _write_deltalake = _load_deltalake()

    dt = delta_table_cls(table_uri, storage_options=opts)
    dt_runtime = dt
    files = dt_runtime.files()
    metadata = dt.metadata()
    version = dt.version()

    total_size_bytes = sum(dt_runtime.get_add_actions().to_pydict().get("size", []))

    return {
        "table_name": table_name,
        "version": version,
        "file_count": len(files),
        "total_size_bytes": total_size_bytes,
        "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
        "table_uri": table_uri,
        "description": metadata.description,
        "partition_columns": metadata.partition_columns,
    }


def list_table_versions(
    table_name: str,
    limit: int = 10,
    storage_options: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """List recent versions of a Delta table.

    Returns up to limit history dicts (most recent first) with version,
    timestamp, operation (e.g. "WRITE", "MERGE"), and operation_parameters
    keys, enabling time travel and audit. Raises an exception if the table
    cannot be accessed.

    Example:
        versions = list_table_versions("raw.events", limit=5)
        for v in versions:
            print(f"v{v['version']}: {v['operation']} at {v['timestamp']}")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)
    delta_table_cls, _write_deltalake = _load_deltalake()

    dt = delta_table_cls(table_uri, storage_options=opts)
    history = dt.history(limit=limit)

    results: list[dict[str, Any]] = []
    for entry in history:
        results.append(
            {
                "version": entry.get("version"),
                "timestamp": entry.get("timestamp"),
                "operation": entry.get("operation"),
                "operation_parameters": entry.get("operationParameters"),
            }
        )
    return results


def rollback_table_to_version(
    table_name: str,
    version: int,
    storage_options: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Roll back a Delta table to a previous version.

    Restores the table to the given historical version via Delta time travel
    restore, creating a new version that matches the target. Returns a dict
    with rolled_back_to set to the restored version number. Raises an
    exception if the rollback operation fails.

    Example:
        result = rollback_table_to_version("raw.events", version=42)
        print(f"Rolled back to version {result['rolled_back_to']}")

    """
    table_uri = _resolve_table_uri(table_name)
    opts = _default_storage_options(storage_options)

    logger.info(
        "delta_table_rollback_started",
        table_name=table_name,
        version=version,
    )
    delta_table_cls, _write_deltalake = _load_deltalake()

    try:
        dt = delta_table_cls(table_uri, storage_options=opts)
        dt.restore(version)
    except Exception as exc:
        logger.error(
            "delta_table_rollback_failed",
            table_name=table_name,
            version=version,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "delta_table_rollback_succeeded",
        table_name=table_name,
        version=version,
    )
    return {"rolled_back_to": version}
