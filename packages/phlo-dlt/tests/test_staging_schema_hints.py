"""Tests for preserving declared schema types during DLT Parquet staging."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import dlt
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pandera.pandas import DataFrameModel, Field

from phlo.logging import get_logger
from phlo_dlt.dlt_helpers import apply_validation_schema_hints
from phlo_dlt.executor import DltIngester
from phlo_dlt.registry import TableConfig


class StagedRecords(DataFrameModel):
    """Schema containing string, nullable string, and timestamp contracts."""

    record_id: str
    observed_at: str
    optional_label: str | None = Field(nullable=True)
    event_time: datetime
    caller_wins: str


def test_validation_schema_hints_preserve_strings_in_staged_parquet(tmp_path: Path) -> None:
    @dlt.resource(name="records")
    def records():
        yield {
            "record_id": None,
            "observed_at": None,
            "optional_label": None,
            "event_time": None,
            "caller_wins": None,
        }
        yield {
            "record_id": "001",
            "observed_at": "2026-01-01T00:00:00Z",
            "optional_label": None,
            "event_time": datetime(2026, 1, 1),
            "caller_wins": "2026-01-01T00:00:00Z",
        }
        yield {
            "record_id": "002",
            "observed_at": "later text",
            "optional_label": "present later",
            "event_time": datetime(2026, 1, 2),
            "caller_wins": "2026-01-02T00:00:00Z",
        }

    resource = records()
    resource.apply_hints(columns={"caller_wins": {"data_type": "timestamp"}})
    apply_validation_schema_hints(resource, StagedRecords)

    pipeline = dlt.pipeline(
        pipeline_name="schema_hint_regression",
        destination=dlt.destinations.filesystem(bucket_url=str(tmp_path / "bucket")),
        dataset_name="raw",
        pipelines_dir=str(tmp_path / "pipelines"),
    )
    pipeline.run(resource, loader_file_format="parquet")

    parquet_files = list((tmp_path / "bucket").rglob("*.parquet"))
    assert len(parquet_files) == 1
    table = pq.read_table(parquet_files[0])
    assert table.schema.field("record_id").type == pa.string()
    assert table.schema.field("observed_at").type == pa.string()
    assert table.schema.field("optional_label").type == pa.string()
    assert pa.types.is_timestamp(table.schema.field("event_time").type)
    assert pa.types.is_timestamp(table.schema.field("caller_wins").type)
    assert table.column("record_id").to_pylist() == [None, "001", "002"]
    assert table.column("observed_at").to_pylist() == [
        None,
        "2026-01-01T00:00:00Z",
        "later text",
    ]
    assert table.column("optional_label").to_pylist() == [None, None, "present later"]


def test_executor_applies_hints_before_staging(monkeypatch, tmp_path: Path) -> None:
    @dlt.resource(name="records")
    def records():
        yield {"observed_at": "2026-01-01T00:00:00Z"}

    resource = records()
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline",
        lambda **_kwargs: (object(), tmp_path),
    )

    def assert_hint_before_staging(**kwargs):
        source = kwargs["dlt_source"]
        assert source.compute_table_schema()["columns"]["observed_at"]["data_type"] == "text"
        raise RuntimeError("staging boundary reached")

    monkeypatch.setattr("phlo_dlt.executor.stage_to_parquet", assert_hint_before_staging)
    ingester = DltIngester(
        context=None,
        logger=get_logger("test_dlt_staging_schema_hints"),
        table_config=TableConfig(
            table_name="records",
            table_schema=None,
            validation_schema=StagedRecords,
            unique_key="record_id",
            group_name="raw",
        ),
        table_store_resource=object(),
        dlt_source_func=lambda **_kwargs: resource,
        validation_schema=StagedRecords,
    )

    with pytest.raises(RuntimeError, match="staging boundary reached"):
        ingester.run_ingestion(partition_key="2026-01-01")
