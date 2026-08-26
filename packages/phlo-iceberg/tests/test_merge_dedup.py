"""Unit tests that merge_to_table applies batch-local deduplication before appending."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pytest

from phlo_iceberg.tables import merge_to_table


class FakeSchemaField:
    def __init__(self, name: str, arrow_type: pa.DataType, nullable: bool = True):
        self.name = name
        self.type = arrow_type
        self.nullable = nullable


class FakeIcebergTable:
    def __init__(self, arrow_schema: pa.Schema):
        self._schema = MagicMock()
        self._schema.fields = [
            FakeSchemaField(field.name, field.type, nullable=field.nullable)
            for field in arrow_schema
        ]
        self.deleted: list = []
        self.appended: list[pa.Table] = []

    def schema(self):
        return self._schema

    def delete(self, expression):
        self.deleted.append(expression)

    def append(self, arrow_table):
        self.appended.append(arrow_table)


def _merge(tmp_path, rows: list[dict], **kwargs):
    data_path = tmp_path / "batch.parquet"
    pd.DataFrame(rows).to_parquet(data_path)

    arrow_schema = pa.schema(
        [
            pa.field("event_id", pa.string()),
            pa.field("updated_at", pa.string()),
            pa.field("status", pa.string()),
        ]
    )
    fake_table = FakeIcebergTable(arrow_schema)

    catalog = MagicMock()
    catalog.load_table.return_value = fake_table

    with (
        patch("phlo_iceberg.tables.get_catalog", return_value=catalog),
        patch(
            "pyiceberg.io.pyarrow.schema_to_pyarrow",
            return_value=arrow_schema,
        ),
    ):
        result = merge_to_table("raw.events", str(data_path), unique_key="event_id", **kwargs)

    return fake_table, result


def test_merge_deduplicates_batch_keys_with_last_by_order_column(tmp_path) -> None:
    fake_table, result = _merge(
        tmp_path,
        [
            {"event_id": "e1", "updated_at": "2024-01-01", "status": "queued"},
            {"event_id": "e1", "updated_at": "2024-03-01", "status": "sent"},
            {"event_id": "e2", "updated_at": "2024-02-01", "status": "open"},
        ],
        deduplication_method="last",
        deduplication_order_by="updated_at",
    )

    appended = pa.concat_tables(fake_table.appended)
    assert appended.num_rows == 2
    statuses = set(appended.column("status").to_pylist())
    assert statuses == {"sent", "open"}
    assert result["rows_inserted"] == 2


def test_merge_without_dedup_options_rejects_unordered_duplicates(tmp_path) -> None:
    with pytest.raises(ValueError, match="requires an explicit ordering column"):
        _merge(
            tmp_path,
            [
                {"event_id": "e1", "updated_at": "2024-01-01", "status": "queued"},
                {"event_id": "e1", "updated_at": "2024-02-01", "status": "sent"},
            ],
        )


def test_merge_appends_all_rows_when_duplicates_absent(tmp_path) -> None:
    fake_table, result = _merge(
        tmp_path,
        [
            {"event_id": "e1", "updated_at": "2024-01-01", "status": "queued"},
            {"event_id": "e2", "updated_at": "2024-02-01", "status": "open"},
        ],
    )

    assert result["rows_inserted"] == 2
    assert len(fake_table.appended[0]) == 2
