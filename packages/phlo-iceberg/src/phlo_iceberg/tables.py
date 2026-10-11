"""Iceberg table management utilities for creating, modifying, and querying tables.

This module provides high-level operations for Iceberg table management including
creating tables, appending/merging data, snapshot management, and cleanup operations.

All table names must be fully qualified as ``namespace.table``.

Example:
    Basic table operations::

        from phlo_iceberg.tables import ensure_table, append_to_table, merge_to_table
        from pyiceberg.schema import Schema
        from pyiceberg.types import NestedField, StringType, LongType

        # Create or get existing table
        schema = Schema(
            NestedField(1, "id", LongType(), required=True),
            NestedField(2, "name", StringType(), required=False),
        )
        table = ensure_table("raw.users", schema=schema)

        # Append data
        result = append_to_table("raw.users", data_path="/data/users.parquet")
        print(f"Inserted {result['rows_inserted']} rows")

        # Merge (upsert) data by unique key
        result = merge_to_table(
            "raw.users",
            data_path="/data/updates.parquet",
            unique_key="id"
        )
        print(f"Updated {result['rows_deleted']} rows, inserted {result['rows_inserted']} rows")

Ported from ``phlo`` core as a capability plugin.

"""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.exceptions import CommitFailedException, TableAlreadyExistsError
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from phlo.capabilities import SAFE_MIN_RETENTION_HOURS, resolve_capability
from phlo.helpers import deduplicate_arrow_by_unique_key
from phlo.logging import get_logger
from phlo.plugins.observatory_settings import get_operational_settings
import phlo.telemetry as phlo_observe
from phlo_iceberg.catalog import create_namespace, get_catalog
from phlo_iceberg.schema_alignment import (
    SchemaPolicy,
    prepare_arrow_write,
    validate_declared_schema,
    validate_schema_policy,
)
from phlo_iceberg.storage import list_storage_files, storage_path_key

logger = get_logger(__name__)


def _require_direct_write(ref: str) -> None:
    """Keep protected main changes on branches until a governed merge."""
    if ref == "main" and get_operational_settings().protect_main:
        raise PermissionError(
            "Direct writes to main are protected; write to a branch and use a signed merge."
        )


def _current_snapshot_id(table) -> str | None:
    """Return the table's current snapshot id; None on read failure or none."""
    try:
        snapshot = table.current_snapshot()
        return str(snapshot.snapshot_id) if snapshot is not None else None
    except Exception:  # noqa: BLE001 - snapshot introspection must not break writes
        return None


def _catalog_system_for_ref() -> str:
    """Return the catalog system that owns refs written by this process.

    The SDK's ``iceberg_commit`` pins branch entities to the ``nessie``
    namespace; under a snapshot-promotion catalog (e.g. polaris) every ref —
    the staging namespace or ``main`` — is owned by that provider, so the
    entity must name the resolved catalog instead. ``nessie`` remains the
    default when no catalog capability is installed.
    """
    try:
        resolution = resolve_capability("catalog")
        name = str(getattr(resolution, "name", "") or "")
        if name:
            return name
    except Exception:  # noqa: BLE001 - entity naming must never break writes
        pass
    return "nessie"


@contextmanager
def _iceberg_commit_scope(
    table_name: str, ref: str, operation: str, **kwargs: Any
) -> Iterator[Any]:
    """Open an ``iceberg.commit`` observation scoped to the ambient run.

    ``snapshot_id_before`` is captured by the caller; ``snapshot_id_after`` is
    filled in by ``_finish_iceberg_commit`` once the commit lands.
    """
    with phlo_observe.iceberg_commit(
        table=table_name,
        branch=ref,
        operation=operation,
        **kwargs,
    ) as commit_op:
        phlo_observe.bind_run_entity(commit_op)
        branch_entity = phlo_observe.branch_entity_id(ref, system=_catalog_system_for_ref())
        if branch_entity:
            with contextlib.suppress(Exception):
                commit_op.set_entity("branch", branch_entity)
        yield commit_op


def _finish_iceberg_commit(scope, table) -> None:
    """Record the post-commit snapshot on the builder; best-effort."""
    snapshot_after = _current_snapshot_id(table)
    if snapshot_after is None:
        return
    try:
        scope.set(snapshot_id_after=snapshot_after)
        scope.set_correlation(snapshot_id=snapshot_after)
    except Exception:  # noqa: BLE001
        pass


def _write_arrow_table(
    table: Table,
    arrow_table: pa.Table,
    *,
    table_name: str,
    schema_policy: SchemaPolicy,
    operation: Literal["append", "merge", "overwrite"],
    unique_key: str | None = None,
) -> int:
    """Publish staged schema and data together; reconcile competing additions."""
    from pyiceberg.expressions import In, Reference

    transaction, aligned, additions = prepare_arrow_write(
        table, arrow_table, table_name=table_name, schema_policy=schema_policy
    )
    for attempt in range(3):
        rows_deleted = 0
        try:
            with transaction as writer:
                if operation == "overwrite":
                    writer.overwrite(aligned)
                else:
                    if operation == "merge":
                        assert unique_key is not None
                        keys = list(set(aligned.column(unique_key).to_pylist()))
                        for start in range(0, len(keys), 1000):
                            batch = keys[start : start + 1000]
                            with warnings.catch_warnings():
                                warnings.filterwarnings(
                                    "ignore",
                                    message=r"^Delete operation did not match any records$",
                                    category=UserWarning,
                                )
                                writer.delete(In(term=Reference(unique_key), values=set(batch)))
                            rows_deleted += len(batch)
                    writer.append(aligned)
            return rows_deleted
        except CommitFailedException as exc:
            # Ordinary data conflicts remain explicit failures as in #1064. Only
            # additive schema races retry, always rebuilding the complete write.
            if not additions or attempt == 2:
                raise
            table.refresh()
            refreshed = schema_to_pyarrow(table.schema())
            for field in additions:
                if field.name in refreshed.names:
                    existing = refreshed.field(field.name)
                    if existing.type != field.type or existing.nullable != field.nullable:
                        raise ValueError(
                            f"Conflicting concurrent definition for {table_name}.{field.name}"
                        ) from exc
            transaction, aligned, _ = prepare_arrow_write(
                table, arrow_table, table_name=table_name, schema_policy=schema_policy
            )
    raise AssertionError("Unreachable write retry state")


def ensure_table(
    table_name: str,
    schema: Schema,
    partition_spec: list[tuple[str, str]] | None = None,
    ref: str = "main",
    *,
    schema_policy: SchemaPolicy = "strict",
) -> Table:
    """Ensure an Iceberg table exists, creating it if necessary.

    Creates the namespace automatically when missing. Raises ValueError on an
    invalid table name or malformed partition spec. If another writer creates
    the table first, its existing handle is returned instead of failing.

    Example:
        Create a partitioned table::

            from pyiceberg.schema import Schema
            from pyiceberg.types import NestedField, LongType, StringType, TimestamptzType

            schema = Schema(
                NestedField(1, "id", LongType(), required=True),
                NestedField(2, "event_time", TimestamptzType(), required=True),
                NestedField(3, "name", StringType(), required=False),
            )

            table = ensure_table(
                "raw.events",
                schema=schema,
                partition_spec=[("event_time", "day")],
                ref="main"
            )
    """
    validate_schema_policy(schema_policy)
    _require_direct_write(ref)
    catalog = get_catalog(ref=ref)

    parts = table_name.split(".")
    if len(parts) != 2:
        raise ValueError(f"Table name must be namespace.table, got: {table_name}")

    namespace, _ = parts

    create_namespace(namespace, ref=ref)

    # Load first so the common path skips creation. A failed load falls through
    # to create_table, and a concurrent creator is resolved by catching
    # TableAlreadyExistsError below and reloading the winner's table.
    try:
        existing = catalog.load_table(table_name)
    except Exception:
        existing = None
    if existing is not None:
        validate_declared_schema(
            existing, schema_to_pyarrow(schema), table_name=table_name, schema_policy=schema_policy
        )
        return existing

    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.transforms import (
        DayTransform,
        HourTransform,
        IdentityTransform,
        MonthTransform,
        YearTransform,
    )

    transform_map = {
        "identity": IdentityTransform(),
        "day": DayTransform(),
        "hour": HourTransform(),
        "month": MonthTransform(),
        "year": YearTransform(),
    }

    # Partition field IDs form their own ID space, separate from schema
    # column IDs; start them high to keep the two visually distinct.
    partition_fields = []
    if partition_spec:
        for field_id, (source_name, transform_name) in enumerate(partition_spec, start=1000):
            source_field = None
            for field in schema.fields:
                if field.name == source_name:
                    source_field = field
                    break

            if not source_field:
                raise ValueError(f"Partition source field not found: {source_name}")

            transform = transform_map.get(transform_name)
            if not transform:
                raise ValueError(f"Unknown transform: {transform_name}")

            partition_fields.append(
                PartitionField(
                    source_id=source_field.field_id,
                    field_id=field_id,
                    transform=transform,
                    name=f"{source_name}_{transform_name}",
                )
            )

    spec = PartitionSpec(*partition_fields) if partition_fields else PartitionSpec()

    try:
        return catalog.create_table(
            identifier=table_name,
            schema=schema,
            partition_spec=spec,
        )
    except TableAlreadyExistsError:
        logger.info("iceberg_table_exists_during_create", table_name=table_name, ref=ref)
        existing = catalog.load_table(table_name)
        validate_declared_schema(
            existing, schema_to_pyarrow(schema), table_name=table_name, schema_policy=schema_policy
        )
        return existing


def append_to_table(
    table_name: str,
    data_path: str | Path,
    ref: str = "main",
    *,
    schema_policy: SchemaPolicy = "strict",
) -> dict[str, int]:
    """Append Parquet data to an Iceberg table.

    Reads from a Parquet file or directory and aligns the data schema to the
    target table, backfilling missing nullable columns with nulls. Raises
    ValueError when required columns are missing. Returns ``rows_inserted``
    statistics; ``rows_deleted`` is always 0.

    Example:
        Append single Parquet file::

            result = append_to_table(
                table_name="raw.events",
                data_path="/data/events_2024-01-01.parquet"
            )
            print(f"Appended {result['rows_inserted']} rows")

        Append directory of Parquet files::

            result = append_to_table(
                table_name="raw.events",
                data_path="/data/daily_batches/",
                ref="main"
            )
    """
    _require_direct_write(ref)
    source_path = str(data_path)
    source_row_count = 0
    rows_inserted = 0

    logger.info(
        "iceberg_table_append_started",
        table_name=table_name,
        ref=ref,
        source=source_path,
        source_row_count=source_row_count,
    )

    try:
        catalog = get_catalog(ref=ref)
        table = catalog.load_table(table_name)

        data_path = Path(data_path) if isinstance(data_path, str) else data_path

        if data_path.is_dir():
            arrow_table = pq.ParquetDataset(str(data_path)).read()
        else:
            arrow_table = pq.read_table(str(data_path))

        source_row_count = len(arrow_table)

        with _iceberg_commit_scope(
            table_name,
            ref,
            "append",
            snapshot_id_before=_current_snapshot_id(table),
            rows_added=len(arrow_table),
        ) as commit_op:
            _write_arrow_table(
                table,
                arrow_table,
                table_name=table_name,
                schema_policy=schema_policy,
                operation="append",
            )
            _finish_iceberg_commit(commit_op, table)
        rows_inserted = len(arrow_table)
        result = {"rows_inserted": rows_inserted, "rows_deleted": 0}
    except Exception as exc:
        logger.error(
            "iceberg_table_append_failed",
            table_name=table_name,
            ref=ref,
            source=source_path,
            source_row_count=source_row_count,
            rows_inserted=rows_inserted,
            rows_deleted=0,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "iceberg_table_append_succeeded",
        table_name=table_name,
        ref=ref,
        source=source_path,
        source_row_count=source_row_count,
        rows_inserted=result["rows_inserted"],
        rows_deleted=result["rows_deleted"],
    )

    return result


def merge_to_table(
    table_name: str,
    data_path: str | Path,
    unique_key: str,
    ref: str = "main",
    *,
    schema_policy: SchemaPolicy = "strict",
    deduplication: bool = True,
    deduplication_method: str | None = None,
    deduplication_order_by: str | None = None,
) -> dict[str, int]:
    """Merge (upsert) Parquet data into an Iceberg table with deduplication.

    Validate and align the batch, then stage all matching-key deletes and the
    append in one transaction. Readers see one atomic catalog publication,
    which may contain multiple snapshots. Precommit failures retain the old
    rows; commit conflicts propagate so callers can retry the complete merge.
    Duplicate keys within the staged batch are first deduplicated
    deterministically (controlled by the ``deduplication*`` arguments);
    duplicates that cannot be resolved fail loudly instead of leaking into
    the destination. Raises ValueError when the unique-key column is absent
    from the source data.

    Delete failures propagate (they no longer pass silently), preventing
    the duplicate-row divergence reported in #777.

    Args:
        table_name: Fully qualified table name in ``namespace.table`` format.
        data_path: Path to Parquet file or directory containing data files.
        unique_key: Column name used to identify matching rows for deletion.
        ref: Nessie branch/tag reference (default: ``main``).
        deduplication: Whether to deduplicate rows sharing a unique key within
            the staged batch before appending (default: True).
        deduplication_method: ``"last"`` (default) or ``"first"``. ``"last"``
            keeps the row with the greatest ``deduplication_order_by`` value per
            key; Parquet row order is never used as a tiebreaker.
        deduplication_order_by: Explicit ordering (version/timestamp) column
            required by ``method="last"`` when duplicates are present.

    Example:
        Upsert user data by ID::

            result = merge_to_table(
                table_name="raw.users",
                data_path="/data/user_updates.parquet",
                unique_key="user_id"
            )
            print(f"Deleted ~{result['rows_deleted']} existing rows")
            print(f"Inserted {result['rows_inserted']} new rows")

    Note:
        ``rows_deleted`` is an approximation: Iceberg's delete does not
        return the actual number of rows removed, only the number of unique
        keys processed.
    """
    _require_direct_write(ref)
    source_path = str(data_path)
    source_row_count = 0
    rows_deleted = 0
    rows_inserted = 0

    logger.info(
        "iceberg_table_merge_started",
        table_name=table_name,
        ref=ref,
        source=source_path,
        source_row_count=source_row_count,
        unique_key=unique_key,
    )

    try:
        catalog = get_catalog(ref=ref)
        table = catalog.load_table(table_name)

        data_path = Path(data_path) if isinstance(data_path, str) else data_path

        if data_path.is_dir():
            arrow_table = pq.ParquetDataset(str(data_path)).read()
        else:
            arrow_table = pq.read_table(str(data_path))

        source_row_count = len(arrow_table)

        if unique_key not in arrow_table.schema.names:
            raise ValueError(
                f"Unique key '{unique_key}' not found in data. "
                f"Available columns: {arrow_table.schema.names}"
            )

        # Validate the entire incoming batch, including rows deduplication might discard.
        prepare_arrow_write(table, arrow_table, table_name=table_name, schema_policy=schema_policy)
        effective_method = deduplication_method or "last"

        if deduplication:
            arrow_table, duplicates_removed = deduplicate_arrow_by_unique_key(
                arrow_table,
                unique_key,
                method=effective_method,
                order_by=deduplication_order_by,
            )
            if duplicates_removed:
                logger.info(
                    "iceberg_merge_batch_deduplicated",
                    table_name=table_name,
                    unique_key=unique_key,
                    method=effective_method,
                    order_by=deduplication_order_by,
                    duplicates_removed=duplicates_removed,
                )
        else:
            key_values = arrow_table.column(unique_key).to_pylist()
            distinct_keys = set(key_values)
            if len(distinct_keys) < len(key_values):
                logger.warning(
                    "source_duplicates_detected_after_deduplication",
                    duplicates_count=len(key_values) - len(distinct_keys),
                    unique_key=unique_key,
                    table_name=table_name,
                )

        with _iceberg_commit_scope(
            table_name,
            ref,
            "merge",
            snapshot_id_before=_current_snapshot_id(table),
            rows_added=len(arrow_table),
        ) as commit_op:
            rows_deleted = _write_arrow_table(
                table,
                arrow_table,
                table_name=table_name,
                schema_policy=schema_policy,
                operation="merge",
                unique_key=unique_key,
            )
            _finish_iceberg_commit(commit_op, table)
            try:
                commit_op.set(rows_removed=rows_deleted)
            except Exception:  # noqa: BLE001
                pass
        rows_inserted = len(arrow_table)

    except Exception as exc:
        logger.error(
            "iceberg_table_merge_failed",
            table_name=table_name,
            ref=ref,
            source=source_path,
            source_row_count=source_row_count,
            unique_key=unique_key,
            rows_deleted=rows_deleted,
            rows_inserted=rows_inserted,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    result = {"rows_deleted": rows_deleted, "rows_inserted": rows_inserted}
    logger.info(
        "iceberg_table_merge_succeeded",
        table_name=table_name,
        ref=ref,
        source=source_path,
        source_row_count=source_row_count,
        unique_key=unique_key,
        rows_deleted=result["rows_deleted"],
        rows_inserted=result["rows_inserted"],
    )

    return result


def overwrite_table(
    table_name: str,
    data_path: str | Path,
    ref: str = "main",
    *,
    schema_policy: SchemaPolicy = "strict",
) -> dict[str, int]:
    """Overwrite an Iceberg table with Parquet data.

    Replaces all existing data in a new snapshot; previous data remains
    accessible via snapshot history until snapshots are expired. Raises
    ValueError when required columns are missing from the source data.

    Example:
        Full table replacement::

            result = overwrite_table(
                table_name="raw.daily_summary",
                data_path="/data/regenerated_summary.parquet"
            )
            print(f"Table now contains {result['rows_inserted']} rows")

    See Also:
        :func:`merge_to_table`: For partial updates without full replacement.
    """
    _require_direct_write(ref)
    source_path = str(data_path)
    source_row_count = 0
    rows_inserted = 0

    logger.info(
        "iceberg_table_overwrite_started",
        table_name=table_name,
        ref=ref,
        source=source_path,
    )

    try:
        catalog = get_catalog(ref=ref)
        table = catalog.load_table(table_name)

        data_path = Path(data_path) if isinstance(data_path, str) else data_path

        if data_path.is_dir():
            arrow_table = pq.ParquetDataset(str(data_path)).read()
        else:
            arrow_table = pq.read_table(str(data_path))

        source_row_count = len(arrow_table)

        with _iceberg_commit_scope(
            table_name,
            ref,
            "overwrite",
            snapshot_id_before=_current_snapshot_id(table),
            rows_added=len(arrow_table),
        ) as commit_op:
            _write_arrow_table(
                table,
                arrow_table,
                table_name=table_name,
                schema_policy=schema_policy,
                operation="overwrite",
            )
            _finish_iceberg_commit(commit_op, table)
        rows_inserted = len(arrow_table)
        result = {"rows_inserted": rows_inserted, "rows_deleted": 0}
    except Exception as exc:
        logger.error(
            "iceberg_table_overwrite_failed",
            table_name=table_name,
            ref=ref,
            source=source_path,
            source_row_count=source_row_count,
            rows_inserted=rows_inserted,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "iceberg_table_overwrite_succeeded",
        table_name=table_name,
        ref=ref,
        source=source_path,
        source_row_count=source_row_count,
        rows_inserted=result["rows_inserted"],
        rows_deleted=result["rows_deleted"],
    )

    return result


def delete_rows_from_table(
    table_name: str,
    predicate: str,
    ref: str = "main",
) -> dict[str, int]:
    """Delete rows matching a predicate expression from an Iceberg table.

    ``predicate`` must be a valid Iceberg SQL expression string. PyIceberg does
    not report deleted row counts, so ``rows_deleted`` is always -1.

    Example:
        Delete old records::

            result = delete_rows_from_table(
                table_name="raw.events",
                predicate="event_time < '2024-01-01T00:00:00Z'"
            )

        Delete by status::

            delete_rows_from_table(
                table_name="raw.users",
                predicate="account_status = 'deleted'"
            )
    """
    _require_direct_write(ref)
    logger.info(
        "iceberg_table_delete_started",
        table_name=table_name,
        ref=ref,
        predicate=predicate,
    )

    try:
        catalog = get_catalog(ref=ref)
        table = catalog.load_table(table_name)
        table.delete(delete_filter=predicate)
    except Exception as exc:
        logger.error(
            "iceberg_table_delete_failed",
            table_name=table_name,
            ref=ref,
            predicate=predicate,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    result = {"rows_deleted": -1}
    logger.info(
        "iceberg_table_delete_succeeded",
        table_name=table_name,
        ref=ref,
        predicate=predicate,
    )

    return result


def list_table_snapshots(
    table_name: str,
    limit: int = 10,
    ref: str = "main",
) -> list[dict]:
    """List recent snapshots of an Iceberg table, most recent first.

    Each entry carries ``snapshot_id``, ``timestamp_ms``, ``operation``, and
    ``summary``.
    """
    catalog = get_catalog(ref=ref)
    table = catalog.load_table(table_name)

    snapshots = sorted(table.snapshots(), key=lambda s: s.timestamp_ms, reverse=True)

    results: list[dict] = []
    for snap in snapshots[:limit]:
        results.append(
            {
                "snapshot_id": snap.snapshot_id,
                "timestamp_ms": snap.timestamp_ms,
                "operation": snap.summary.operation.value if snap.summary else None,
                "summary": dict(snap.summary.additional_properties) if snap.summary else {},
            }
        )

    return results


class StaleTableRevision(ValueError):
    """The table changed since the caller confirmed its revision."""


def rollback_table_to_snapshot(
    table_name: str,
    snapshot_id: int,
    ref: str = "main",
    *,
    expected_metadata_location: str | None = None,
) -> dict:
    """Roll back an Iceberg table to a previous snapshot.

    Returns a dict with the ``rolled_back_to`` snapshot ID.
    """
    _require_direct_write(ref)
    logger.info(
        "iceberg_table_rollback_started",
        table_name=table_name,
        ref=ref,
        snapshot_id=snapshot_id,
    )

    try:
        catalog = get_catalog(ref=ref)
        table = catalog.load_table(table_name)
        if (
            expected_metadata_location is not None
            and table.metadata_location != expected_metadata_location
        ):
            raise StaleTableRevision("Table revision changed; refresh snapshot history.")
        table.manage_snapshots().rollback_to_snapshot(snapshot_id).commit()
    except Exception as exc:
        logger.error(
            "iceberg_table_rollback_failed",
            table_name=table_name,
            ref=ref,
            snapshot_id=snapshot_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise

    logger.info(
        "iceberg_table_rollback_succeeded",
        table_name=table_name,
        ref=ref,
        snapshot_id=snapshot_id,
    )

    return {"rolled_back_to": snapshot_id}


def get_table_schema(table_name: str, ref: str = "main") -> Schema:
    """Get the current schema of an Iceberg table.

    Example:
        Inspect table structure::

            schema = get_table_schema("raw.events")
            for field in schema.fields:
                print(f"{field.name}: {field.field_type}")
    """
    catalog = get_catalog(ref=ref)
    table = catalog.load_table(table_name)
    return table.schema()


def delete_table(table_name: str, ref: str = "main") -> None:
    """Permanently delete an Iceberg table from the catalog.

    Warning:
        This operation is irreversible. While the underlying data files
        may persist in storage until cleanup, the table metadata is
        permanently removed from the catalog.

    Example:
        Remove table with confirmation::

            delete_table("raw.temp_data", ref="main")
            print("Table deleted from catalog")

    See Also:
        :func:`remove_orphan_files`: To clean up underlying storage files.
    """
    _require_direct_write(ref)
    catalog = get_catalog(ref=ref)
    catalog.drop_table(table_name)


def expire_snapshots(
    table_name: str,
    older_than_days: int | None = None,
    retain_last: int = 5,
    ref: str = "main",
    *,
    older_than_hours: int | None = None,
) -> dict[str, int]:
    """Expire old snapshots from an Iceberg table.

    ``older_than_days`` and ``older_than_hours`` are mutually exclusive and
    default to 7 days when neither is set; ``retain_last`` always keeps at least
    that many snapshots. Returns a dict with the ``deleted_snapshots`` count.
    Raise ValueError when both cutoffs are set, retention is non-positive,
    retain_last < 0, or the table name is invalid.
    """
    if older_than_days is not None and older_than_hours is not None:
        raise ValueError("Specify older_than_days or older_than_hours, not both")
    if older_than_hours is not None:
        if older_than_hours <= 0:
            raise ValueError(f"older_than_hours must be positive, got {older_than_hours}")
        if older_than_hours < SAFE_MIN_RETENTION_HOURS:
            raise ValueError("Snapshot retention cannot be less than the 7-day safety floor")
    else:
        effective_days = older_than_days if older_than_days is not None else 7
        if effective_days <= 0:
            raise ValueError(f"older_than_days must be positive, got {effective_days}")
        if effective_days * 24 < SAFE_MIN_RETENTION_HOURS:
            raise ValueError("Snapshot retention cannot be less than the 7-day safety floor")
    if retain_last < 1:
        raise ValueError(f"retain_last must be at least 1, got {retain_last}")
    if "." not in table_name:
        raise ValueError(f"table_name must be namespace.table format, got {table_name}")
    # Validation above runs before this refusal so callers with bad retention
    # arguments get a precise error instead of the generic "disabled" one.
    raise ValueError(
        "Direct snapshot expiry is disabled; use IcebergResource.expire_snapshots "
        "for plan-first retention maintenance"
    )


def _orphan_retention(older_than_days: int | None, older_than_hours: int | None) -> timedelta:
    """Return the orphan retention window, enforcing the 7-day safety floor.

    Raises: ValueError when both cutoffs are set or retention is non-positive or too short.
    """
    if older_than_days is not None and older_than_hours is not None:
        raise ValueError("Specify older_than_days or older_than_hours, not both")
    if older_than_hours is not None:
        name, value, hours = "older_than_hours", older_than_hours, older_than_hours
    else:
        value = older_than_days if older_than_days is not None else 7
        name, hours = "older_than_days", value * 24
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    if hours < SAFE_MIN_RETENTION_HOURS:
        raise ValueError("Orphan retention cannot be less than the 7-day safety floor")
    return timedelta(hours=hours)


def _referenced_files(table: Table) -> set[str]:
    """Return every manifest and data file path referenced by any snapshot."""
    referenced_files: set[str] = set()
    for snapshot in table.snapshots():
        for manifest in snapshot.manifests(table.io):
            referenced_files.add(manifest.manifest_path)
            for entry in manifest.fetch_manifest_entry(table.io):
                referenced_files.add(entry.data_file.file_path)
    return referenced_files


def _is_older_than(file_info: Any, cutoff: datetime) -> bool:
    """Return whether a listed file predates the cutoff; files without an mtime count as old."""
    mtime = getattr(file_info, "mtime", None)
    return not mtime or mtime < cutoff


def _unreferenced_data_files(table: Table, older_than: datetime) -> list[str]:
    """List unreferenced data files older than the cutoff; return [] when listing fails."""
    referenced_files = _referenced_files(table)
    table_location = table.location()
    try:
        normalized_references = {storage_path_key(path) for path in referenced_files}
        return [
            file_info.path
            for file_info in list_storage_files(table.io, f"{table_location}/data")
            if storage_path_key(str(file_info.path)) not in normalized_references
            and _is_older_than(file_info, older_than)
        ]
    except Exception as e:
        logger.warning("orphan_file_listing_failed", table_location=table_location, error=str(e))
        return []


def remove_orphan_files(
    table_name: str,
    older_than_days: int | None = None,
    dry_run: bool = True,
    ref: str = "main",
    *,
    older_than_hours: int | None = None,
) -> dict[str, int | list[str] | bool]:
    """Remove orphan files not referenced by any snapshot.

    Direct destructive deletion is disabled here. Use the provider-neutral
    ``IcebergResource.cleanup_orphan_files`` contract for dry-run discovery;
    destructive execution is refused on the blessed provider boundary.
    ``older_than_days`` and ``older_than_hours`` are mutually exclusive and
    default to 7 days when neither is set. Returns orphan counts and file lists.
    Raise ValueError when both cutoffs are set, retention is non-positive, or
    the table name is invalid.
    """
    retention = _orphan_retention(older_than_days, older_than_hours)
    if "." not in table_name:
        raise ValueError(f"table_name must be namespace.table format, got {table_name}")
    if not dry_run:
        raise ValueError(
            "Direct orphan deletion is disabled; use IcebergResource.cleanup_orphan_files"
        )

    table = get_catalog(ref=ref).load_table(table_name)
    orphan_files = _unreferenced_data_files(table, datetime.now(timezone.utc) - retention)

    logger.info(
        "orphan_files_found_dry_run",
        table_name=table_name,
        orphan_file_count=len(orphan_files),
    )
    return {
        "orphan_count": len(orphan_files),
        "orphan_files": orphan_files[:100],  # Limit list size
        "dry_run": dry_run,
    }


def get_table_stats(table_name: str, ref: str = "main", *, table: Table | None = None) -> dict:
    """Get statistics about an Iceberg table.

    Returns a dict with ``snapshot_count``, ``file_count``, and
    ``total_size_bytes``, among other fields.
    """
    if table is None:
        catalog = get_catalog(ref=ref)
        table = catalog.load_table(table_name)

    snapshots = list(table.snapshots())
    snapshot_count = len(snapshots)

    file_count = 0
    total_size_bytes = 0
    total_records = 0

    current_snapshot = table.current_snapshot()
    if current_snapshot:
        for manifest in current_snapshot.manifests(table.io):
            for entry in manifest.fetch_manifest_entry(table.io):
                file_count += 1
                total_size_bytes += entry.data_file.file_size_in_bytes
                total_records += entry.data_file.record_count

    return {
        "table_name": table_name,
        "snapshot_count": snapshot_count,
        "current_snapshot_id": (
            int(current_snapshot.snapshot_id) if current_snapshot is not None else None
        ),
        "file_count": file_count,
        "total_size_bytes": total_size_bytes,
        "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
        "total_records": total_records,
        "location": table.location(),
    }
