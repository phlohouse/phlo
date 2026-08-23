"""Delta Lake resource wrapper for Phlo table store interface.

This module provides the DeltaResource class that wraps Delta Lake table
operations and integrates with the Phlo resource provider system. It handles
table lifecycle operations, data ingestion, and maintenance operations.

Example:
    from phlo_delta.resource import DeltaResource

    resource = DeltaResource()
    table = resource.get_table("raw.events")
    resource.append_parquet("raw.events", "/data/events.parquet")

Table-store adapter boundary: implements the TableStoreSupport contract from
phlo.capabilities.interfaces; no repository module imports it directly.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import pyarrow as pa
from pandera.pandas import DataFrameModel

from phlo.capabilities.interfaces import TableStoreSupport
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo_delta.settings import get_settings
from phlo_delta.tables import (
    append_to_table,
    delete_rows_from_table,
    ensure_table,
    list_table_versions,
    merge_to_table,
    overwrite_table,
    remove_orphan_files,
    rollback_table_to_version,
)

logger = get_logger(__name__)


def _load_delta_table() -> type[Any]:
    """Load the optional DeltaTable runtime lazily to avoid an import-time
    dependency on deltalake.
    """
    return cast(Any, importlib.import_module("deltalake")).DeltaTable


def _resolve_delta_ref(override_ref: str | None) -> None:
    """Validate the override ref for Delta operations: accept ``None``/``main``
    for table-store interface compatibility and reject branch-like overrides,
    since Delta tables in Phlo are not branch-aware.

    Raises PhloConfigError when an unsupported override_ref is provided.

    Example:
        _resolve_delta_ref(None)  # OK
        _resolve_delta_ref("main")  # OK
        _resolve_delta_ref("dev")  # Raises PhloConfigError

    """
    if override_ref in (None, "", "main"):
        return
    raise PhloConfigError(
        message=f"Delta table_store does not support refs; got override_ref={override_ref!r}",
        suggestions=[
            "Use the default main ref when writing to Delta tables",
            "Use resource.support.supports_refs to branch behaviour before calling table-store operations",
            "Use phlo-iceberg if you need Nessie branch-aware table writes",
        ],
    )


def _partition_columns_from_spec(
    partition_spec: Sequence[tuple[str, str] | str] | None,
) -> list[str] | None:
    """Convert shared partition_spec tuples into Delta partition columns;
    returns None when there is no partitioning.

    Delta Lake only supports identity partitioning here, so transforms such as
    ``day`` or ``bucket`` must be rejected explicitly. Raises PhloConfigError
    on invalid spec format or unsupported transforms.

    Example:
        cols = _partition_columns_from_spec([("date", "identity")])
        # Returns: ["date"]

    """
    if not partition_spec:
        return None

    partition_columns: list[str] = []
    for entry in partition_spec:
        if isinstance(entry, str):
            partition_columns.append(entry)
            continue

        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise PhloConfigError(
                message="Delta partition_spec entries must be column names or (column, transform) pairs",
                suggestions=[
                    "Use partition_spec=[('column', 'identity')] for Delta tables",
                    "Or omit partition_spec entirely for unpartitioned Delta tables",
                ],
            )

        source_name, transform_name = entry
        if not isinstance(source_name, str) or not isinstance(transform_name, str):
            raise PhloConfigError(
                message="Delta partition_spec entries must contain string column and transform names",
                suggestions=[
                    "Use partition_spec=[('column', 'identity')] for Delta tables",
                ],
            )
        if transform_name != "identity":
            raise PhloConfigError(
                message=f"Delta table_store only supports identity partition transforms, got {transform_name!r}",
                suggestions=[
                    "Use partition_spec=[('column', 'identity')] with Delta",
                    "Use phlo-iceberg for transform-based partitioning like day/month/bucket",
                ],
            )
        partition_columns.append(source_name)

    return partition_columns


@dataclass
class DeltaResource:
    """Resource wrapper implementing the Phlo table-store protocol for Delta Lake.

    Example:
        resource = DeltaResource()
        table = resource.ensure_table("raw.events", schema)
        stats = resource.append_parquet("raw.events", "/data/file.parquet")

    """

    @property
    def support(self) -> TableStoreSupport:
        """Return Delta table-store support metadata."""
        return TableStoreSupport(
            supports_refs=False,
            partition_transforms=frozenset({"identity"}),
            supports_snapshots=True,
            supports_compaction=True,
            supports_vacuum=True,
        )

    def table_uri(self, table_name: str) -> str:
        """Construct the full S3 URI for a fully qualified table name.

        Example:
            uri = resource.table_uri("raw.events")
            # Returns: "s3://lake/warehouse/delta/raw/events"

        """
        from phlo_delta.tables import _resolve_table_uri

        return _resolve_table_uri(table_name)

    def get_table(self, table_name: str) -> Any:
        """Return a configured DeltaTable handle for a fully qualified table name.

        Example:
            table = resource.get_table("raw.events")
            version = table.version()

        """
        from deltalake import DeltaTable

        from phlo_delta.tables import _resolve_table_uri

        table_uri = _resolve_table_uri(table_name)
        opts = get_settings().get_storage_options()
        return DeltaTable(table_uri, storage_options=opts)

    def schema_from_validation_schema(
        self, validation_schema: type[DataFrameModel] | type[Any]
    ) -> pa.Schema:
        """Convert a Pandera DataFrameModel into a PyArrow schema suitable for
        Delta Lake table creation.

        Example:
            from my_schemas import EventSchema
            schema = resource.schema_from_validation_schema(EventSchema)

        """
        from phlo_delta.schema_conversion import pandera_to_delta

        return pandera_to_delta(validation_schema)

    def ensure_table(
        self,
        table_name: str,
        schema: pa.Schema,
        partition_spec: Sequence[tuple[str, str] | str] | None = None,
        override_ref: str | None = None,
    ) -> Any:
        """Create the table if missing and return its DeltaTable handle.

        Raises PhloConfigError when an unsupported override_ref is provided.

        Example:
            table = resource.ensure_table(
                "raw.events",
                schema,
                partition_spec=[("date", "identity")]
            )

        """
        _resolve_delta_ref(override_ref)
        return ensure_table(
            table_name=table_name,
            schema=schema,
            partition_columns=_partition_columns_from_spec(partition_spec),
        )

    def append_parquet(
        self,
        table_name: str,
        data_path: str,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Append parquet data from data_path into the table and return write
        statistics (rows_inserted, rows_deleted).

        Raises PhloConfigError when an unsupported override_ref is provided;
        append failures propagate after logging.

        Example:
            result = resource.append_parquet("raw.events", "/data/events.parquet")
            print(f"Inserted {result['rows_inserted']} rows")

        """
        _resolve_delta_ref(override_ref)
        logger.info(
            "delta_resource_append_requested",
            table_name=table_name,
            source=data_path,
        )
        try:
            result = append_to_table(table_name=table_name, data_path=data_path)
        except Exception as exc:
            logger.error(
                "delta_resource_append_failed",
                table_name=table_name,
                source=data_path,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise
        logger.info(
            "delta_resource_append_completed",
            table_name=table_name,
            source=data_path,
            rows_inserted=result.get("rows_inserted", 0),
            rows_deleted=result.get("rows_deleted", 0),
        )
        return result

    def merge_parquet(
        self,
        table_name: str,
        data_path: str,
        unique_key: str,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Upsert parquet data into the table keyed by unique_key and return
        write statistics (rows_inserted, rows_updated, rows_deleted).

        Raises PhloConfigError when an unsupported override_ref is provided;
        merge failures propagate after logging.

        Example:
            result = resource.merge_parquet(
                "raw.events", "/data/events.parquet", unique_key="event_id"
            )

        """
        _resolve_delta_ref(override_ref)
        logger.info(
            "delta_resource_merge_requested",
            table_name=table_name,
            source=data_path,
            unique_key=unique_key,
        )
        try:
            result = merge_to_table(
                table_name=table_name,
                data_path=data_path,
                unique_key=unique_key,
            )
        except Exception as exc:
            logger.error(
                "delta_resource_merge_failed",
                table_name=table_name,
                source=data_path,
                unique_key=unique_key,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise
        logger.info(
            "delta_resource_merge_completed",
            table_name=table_name,
            source=data_path,
            unique_key=unique_key,
            rows_inserted=result.get("rows_inserted", 0),
            rows_deleted=result.get("rows_deleted", 0),
        )
        return result

    def overwrite_parquet(
        self,
        *,
        table_name: str,
        data_path: str,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Replace all table data with parquet from data_path and return write
        statistics.

        Raises PhloConfigError when an unsupported override_ref is provided;
        overwrite failures propagate after logging.

        Example:
            result = resource.overwrite_parquet(
                table_name="raw.events",
                data_path="/data/events.parquet"
            )

        """
        _resolve_delta_ref(override_ref)
        logger.info(
            "delta_resource_overwrite_requested",
            table_name=table_name,
            source=data_path,
        )
        try:
            result = overwrite_table(table_name=table_name, data_path=data_path)
        except Exception as exc:
            logger.error(
                "delta_resource_overwrite_failed",
                table_name=table_name,
                source=data_path,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise
        logger.info(
            "delta_resource_overwrite_completed",
            table_name=table_name,
            source=data_path,
            rows_inserted=result.get("rows_inserted", 0),
            rows_deleted=result.get("rows_deleted", 0),
        )
        return result

    def delete_rows(
        self,
        *,
        table_name: str,
        predicate: str,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Delete rows matching a SQL predicate; rows_deleted is -1 because
        predicate deletes do not return a count.

        Raises PhloConfigError when an unsupported override_ref is provided;
        delete failures propagate after logging.

        Example:
            result = resource.delete_rows(
                table_name="raw.events",
                predicate="created_at < '2024-01-01'"
            )

        """
        _resolve_delta_ref(override_ref)
        logger.info(
            "delta_resource_delete_rows_requested",
            table_name=table_name,
            predicate=predicate,
        )
        try:
            result = delete_rows_from_table(table_name=table_name, predicate=predicate)
        except Exception as exc:
            logger.error(
                "delta_resource_delete_rows_failed",
                table_name=table_name,
                predicate=predicate,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise
        logger.info(
            "delta_resource_delete_rows_completed",
            table_name=table_name,
            predicate=predicate,
        )
        return result

    def compact(self, *, table_name: str) -> dict[str, object]:
        """Coalesce small files into larger ones via Delta OPTIMIZE.

        Example:
            result = resource.compact(table_name="raw.events")
            print(f"Compaction metrics: {result['compaction']}")

        """
        from phlo_delta.tables import _resolve_table_uri

        table_uri = _resolve_table_uri(table_name)
        opts = get_settings().get_storage_options()
        delta_table_cls = _load_delta_table()

        logger.info("delta_resource_compact_requested", table_name=table_name)
        dt = delta_table_cls(table_uri, storage_options=opts)
        result = dt.optimize.compact()
        logger.info("delta_resource_compact_completed", table_name=table_name)
        return {"compaction": result}

    def list_snapshots(self, *, table_name: str, limit: int = 10) -> list[dict]:
        """Return version-history metadata dicts (version, timestamp, operation,
        parameters), most recent first.

        Example:
            versions = resource.list_snapshots(table_name="raw.events", limit=5)
            for v in versions:
                print(f"Version {v['version']}: {v['operation']}")

        """
        return list_table_versions(table_name=table_name, limit=limit)

    def rollback_to_snapshot(self, *, table_name: str, snapshot_id: int | str) -> dict:
        """Restore a table to a historical version via time travel, returning
        ``{"rolled_back_to": <version>}``; rollback failures propagate after
        logging.

        Example:
            result = resource.rollback_to_snapshot(
                table_name="raw.events",
                snapshot_id=42
            )
            print(f"Rolled back to version {result['rolled_back_to']}")

        """
        logger.info(
            "delta_resource_rollback_requested",
            table_name=table_name,
            version=snapshot_id,
        )
        try:
            result = rollback_table_to_version(table_name=table_name, version=int(snapshot_id))
        except Exception as exc:
            logger.error(
                "delta_resource_rollback_failed",
                table_name=table_name,
                version=snapshot_id,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise
        logger.info(
            "delta_resource_rollback_completed",
            table_name=table_name,
            version=snapshot_id,
        )
        return result

    def vacuum(self, *, table_name: str, retain_hours: int = 168) -> dict:
        """Delete data files no longer needed past retain_hours (default 7 days),
        returning files_removed count and removed_files list; vacuum failures
        propagate after logging.

        Example:
            result = resource.vacuum(table_name="raw.events", retain_hours=72)
            print(f"Removed {result['files_removed']} files")

        """
        logger.info(
            "delta_resource_vacuum_requested",
            table_name=table_name,
            retain_hours=retain_hours,
        )
        result = remove_orphan_files(table_name=table_name, retain_hours=retain_hours)
        logger.info(
            "delta_resource_vacuum_completed",
            table_name=table_name,
            files_removed=result["files_removed"],
        )
        return result
