"""Tests for phlo_delta.helpers: identity partitioning, schema loading,
and table maintenance recommendations."""

from unittest.mock import MagicMock, patch

import pyarrow as pa

from phlo_delta.helpers import (
    identity_partition,
    load_table_schema,
    maintenance_recommendations,
    recommend_table_maintenance,
    table_exists,
)


def test_table_exists_returns_true_when_delta_table_opens() -> None:
    delta_table_cls = MagicMock()

    with (
        patch("phlo_delta.tables._load_deltalake", return_value=(delta_table_cls, MagicMock())),
        patch("phlo_delta.tables._resolve_table_uri", return_value="s3://lake/raw/events"),
        patch(
            "phlo_delta.tables._default_storage_options", return_value={"AWS_REGION": "eu-west-1"}
        ),
    ):
        assert table_exists("raw.events") is True

    delta_table_cls.assert_called_once_with(
        "s3://lake/raw/events",
        storage_options={"AWS_REGION": "eu-west-1"},
    )


def test_table_exists_returns_false_when_delta_table_open_fails() -> None:
    delta_table_cls = MagicMock(side_effect=Exception("missing"))

    with (
        patch("phlo_delta.tables._load_deltalake", return_value=(delta_table_cls, MagicMock())),
        patch("phlo_delta.tables._resolve_table_uri", return_value="s3://lake/raw/missing"),
        patch("phlo_delta.tables._default_storage_options", return_value={}),
    ):
        assert table_exists("raw.missing") is False


def test_load_table_schema_returns_pyarrow_schema() -> None:
    schema = pa.schema([pa.field("id", pa.string())])
    table = MagicMock()
    table.schema.return_value.to_pyarrow.return_value = schema
    delta_table_cls = MagicMock(return_value=table)

    with (
        patch("phlo_delta.tables._load_deltalake", return_value=(delta_table_cls, MagicMock())),
        patch("phlo_delta.tables._resolve_table_uri", return_value="s3://lake/raw/events"),
        patch("phlo_delta.tables._default_storage_options", return_value={}),
    ):
        assert load_table_schema("raw.events") == schema


def test_identity_partition_returns_delta_partition_columns() -> None:
    assert identity_partition("tenant_id", "region") == ["tenant_id", "region"]


def test_maintenance_recommendations_from_stats() -> None:
    stats = {"file_count": 1200, "total_size_mb": 120.0}

    assert maintenance_recommendations(
        stats,
        max_file_count=1000,
        min_avg_file_size_mb=32.0,
    ) == ["vacuum", "consider_optimize"]


def test_recommend_table_maintenance_loads_stats() -> None:
    with patch(
        "phlo_delta.tables.get_table_stats",
        return_value={"file_count": 2, "total_size_mb": 1.0},
    ) as get_stats:
        recommendations = recommend_table_maintenance(
            "raw.events",
            storage_options={"AWS_REGION": "eu-west-1"},
        )

    get_stats.assert_called_once_with(
        "raw.events",
        storage_options={"AWS_REGION": "eu-west-1"},
    )
    assert recommendations == ["consider_optimize"]
