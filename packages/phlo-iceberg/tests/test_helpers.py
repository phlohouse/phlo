"""Tests for phlo_iceberg.helpers: identity partitioning, schema loading,
and table maintenance recommendations."""

from unittest.mock import MagicMock, patch

import pytest

from phlo_iceberg.helpers import (
    identity_partition,
    load_table_schema,
    maintenance_recommendations,
    partition_spec,
    recommend_table_maintenance,
    table_exists,
    temporal_partition,
)
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType


def test_table_exists_returns_true_when_catalog_loads_table() -> None:
    catalog = MagicMock()
    catalog.load_table.return_value = MagicMock()

    with patch("phlo_iceberg.catalog.get_catalog", return_value=catalog) as get_catalog:
        assert table_exists("raw.events", ref="dev") is True

    get_catalog.assert_called_once_with(ref="dev")
    catalog.load_table.assert_called_once_with("raw.events")


def test_table_exists_returns_false_when_catalog_load_fails() -> None:
    catalog = MagicMock()
    catalog.load_table.side_effect = Exception("missing")

    with patch("phlo_iceberg.catalog.get_catalog", return_value=catalog):
        assert table_exists("raw.missing") is False


def test_load_table_schema_delegates_to_tables_module() -> None:
    schema = Schema(NestedField(1, "id", StringType(), required=True))

    with patch("phlo_iceberg.tables.get_table_schema", return_value=schema) as get_schema:
        assert load_table_schema("raw.events", ref="dev") is schema

    get_schema.assert_called_once_with("raw.events", ref="dev")


def test_partition_helpers_build_valid_specs() -> None:
    assert identity_partition("tenant_id", "region") == [
        ("tenant_id", "identity"),
        ("region", "identity"),
    ]
    assert temporal_partition("event_time", "hour") == [("event_time", "hour")]
    assert partition_spec(("tenant_id", "identity"), ("event_time", "day")) == [
        ("tenant_id", "identity"),
        ("event_time", "day"),
    ]


def test_partition_helpers_reject_invalid_transform() -> None:
    with pytest.raises(ValueError, match="Unknown Iceberg partition transform"):
        partition_spec(("event_time", "bucket"))  # type: ignore[arg-type]


def test_maintenance_recommendations_from_stats() -> None:
    stats = {
        "file_count": 1200,
        "snapshot_count": 75,
        "total_size_mb": 120.0,
    }

    assert maintenance_recommendations(
        stats,
        max_file_count=1000,
        max_snapshot_count=50,
        min_avg_file_size_mb=32.0,
    ) == ["expire_snapshots", "remove_orphan_files", "consider_compaction"]


def test_recommend_table_maintenance_loads_stats() -> None:
    with patch(
        "phlo_iceberg.tables.get_table_stats",
        return_value={"file_count": 2, "snapshot_count": 1, "total_size_mb": 1.0},
    ) as get_stats:
        recommendations = recommend_table_maintenance("raw.events", ref="dev")

    get_stats.assert_called_once_with("raw.events", ref="dev")
    assert recommendations == ["consider_compaction"]
