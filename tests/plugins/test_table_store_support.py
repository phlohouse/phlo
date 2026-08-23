"""Tests for table-store support metadata.

TableStoreSupport defaults to basic writes only (identity partitioning, no
refs/snapshots/compaction/vacuum), reports per-transform partition support,
and a minimal store implementing the core methods satisfies the TableStore
protocol via its support property.
"""

from phlo.capabilities.interfaces import TableStore, TableStoreSupport


def test_table_store_support_defaults_to_basic_writes() -> None:
    support = TableStoreSupport()

    assert support.supports_refs is False
    assert support.partition_transforms == frozenset({"identity"})
    assert support.supports_snapshots is False
    assert support.supports_compaction is False
    assert support.supports_vacuum is False


def test_table_store_support_reports_partition_transform_support() -> None:
    support = TableStoreSupport(partition_transforms=frozenset({"identity", "day"}))

    assert support.supports_partition_transform("identity") is True
    assert support.supports_partition_transform("bucket") is False


class MinimalStore:
    support = TableStoreSupport()

    def ensure_table(self, *, table_name, schema, partition_spec=None, override_ref=None):
        return object()

    def append_parquet(self, *, table_name, data_path, override_ref=None):
        return {"rows_inserted": 1}

    def merge_parquet(self, *, table_name, data_path, unique_key, override_ref=None):
        return {"rows_inserted": 1, "rows_deleted": 0}

    def overwrite_parquet(self, *, table_name, data_path, override_ref=None):
        return {"rows_inserted": 1}

    def delete_rows(self, *, table_name, predicate, override_ref=None):
        return {"rows_deleted": 1}

    def compact(self, *, table_name, override_ref=None):
        return {}

    def list_snapshots(self, *, table_name, limit=10):
        return []

    def rollback_to_snapshot(self, *, table_name, snapshot_id):
        return {}

    def vacuum(self, *, table_name, retain_hours=168):
        return {}


def test_table_store_protocol_accepts_support_property() -> None:
    assert isinstance(MinimalStore(), TableStore)
