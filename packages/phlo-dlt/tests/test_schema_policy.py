"""Public ingestion preserves drift for the opted-in Iceberg write boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import dlt
import phlo
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pandera.pandas import DataFrameModel
from pyiceberg.catalog.sql import SqlCatalog

from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets
from phlo_dlt.dlt_helpers import merge_to_table_store
from phlo_dlt.registry import TableConfig
from phlo_iceberg.resource import IcebergResource


@pytest.mark.parametrize("policy", ["strict", "additive", "drop_extra"])
@pytest.mark.parametrize("strategy", ["append", "merge"])
def test_public_decorator_real_dlt_staging_and_catalog(tmp_path, monkeypatch, policy, strategy):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    catalog = SqlCatalog(
        "ingestion", uri=f"sqlite:///{tmp_path / 'catalog.db'}", warehouse=warehouse.as_uri()
    )
    for module in ("tables", "resource", "catalog"):
        monkeypatch.setattr(f"phlo_iceberg.{module}.get_catalog", lambda ref: catalog)
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability",
        lambda runtime: (IcebergResource(), "iceberg"),
    )

    def setup_pipeline(pipeline_name, dataset_name):
        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            destination=dlt.destinations.filesystem(bucket_url=str(tmp_path / "bucket")),
            dataset_name=dataset_name,
            pipelines_dir=str(tmp_path / "pipelines"),
        )
        return pipeline, tmp_path / "pipelines" / pipeline_name

    monkeypatch.setattr("phlo_dlt.executor.setup_dlt_pipeline", setup_pipeline)

    class Watering(DataFrameModel):
        event_id: int
        bed_id: str
        litres: float

    rows = [{"event_id": 1, "bed_id": "old", "litres": 2.5}]
    clear_ingestion_assets()

    @phlo.ingest.dlt(
        table_name="watering",
        unique_key="event_id",
        group="raw",
        validation_schema=Watering,
        validate=False,
        strict_validation=False,
        schema_policy=policy,
        merge_strategy=strategy,
    )
    def watering(partition_date):
        yield from rows

    assert watering._phlo_table_config.schema_policy == policy
    runtime = SimpleNamespace(
        run_id="schema-policy",
        partition_key="2026-10-07",
        tags={"phlo/ref": "main", "phlo/project_id": "schema-policy-test"},
        resources={},
        logger=get_logger("schema-policy"),
    )

    def run():
        return list(get_ingestion_assets()[0].run.fn(runtime))

    try:
        run()
        table = catalog.load_table("raw.watering")
        original_ids = [(field.name, field.field_id) for field in table.schema().fields]
        before_location = table.metadata_location
        before_rows = table.scan().to_arrow().to_pylist()
        rows[:] = [{"event_id": 2, "bed_id": "new", "litres": 4.0, "watering_method": "drip"}]
        if policy == "strict":
            with pytest.raises(ValueError, match="Unexpected columns.*watering_method"):
                run()
            after = catalog.load_table("raw.watering")
            assert after.metadata_location == before_location
            assert after.scan().to_arrow().to_pylist() == before_rows
        else:
            run()
            after = catalog.load_table("raw.watering")
            assert [
                (f.name, f.field_id) for f in after.schema().fields[: len(original_ids)]
            ] == original_ids
            result = sorted(after.scan().to_arrow().to_pylist(), key=lambda row: row["event_id"])
            assert [(r["event_id"], r["bed_id"], r["litres"]) for r in result] == [
                (1, "old", 2.5),
                (2, "new", 4.0),
            ]
            if policy == "additive":
                assert [r["watering_method"] for r in result] == [None, "drip"]
            else:
                assert "watering_method" not in after.schema().column_names
            # DLT puts incompatible values into a variant column and nulls the
            # original required key. Both policies must reject that staged row.
            rows[:] = [{"event_id": "not-an-integer", "bed_id": "bad", "litres": 1.0}]
            before = after.metadata_location
            with pytest.raises(
                ValueError, match="Required target column 'event_id' contains nulls"
            ):
                run()
            assert catalog.load_table("raw.watering").metadata_location == before
    finally:
        clear_ingestion_assets()
        catalog.engine.dispose()


@pytest.mark.parametrize("policy", ["additive", "drop_extra"])
def test_non_opted_in_provider_refuses_policy_before_any_write(tmp_path, policy):
    provider = SimpleNamespace(ensure_table=MagicMock(), append_parquet=MagicMock())
    config = TableConfig(
        "events", pa.schema([pa.field("id", pa.int64())]), None, "id", "raw", schema_policy=policy
    )
    with pytest.raises(PhloConfigError, match="does not support explicit schema policies"):
        merge_to_table_store(SimpleNamespace(), provider, config, [], "main", "append")
    provider.ensure_table.assert_not_called()
    provider.append_parquet.assert_not_called()


def test_provider_without_opt_in_retains_default_projection(tmp_path):
    path = tmp_path / "source.parquet"
    pq.write_table(pa.table({"id": [7], "extra": ["legacy"]}), path)
    written = []
    provider = SimpleNamespace(
        ensure_table=MagicMock(),
        append_parquet=lambda **kwargs: (
            written.append(pq.read_table(kwargs["data_path"])) or {"rows_inserted": 1}
        ),
    )
    config = TableConfig("events", pa.schema([pa.field("id", pa.int64())]), None, "id", "raw")
    context = SimpleNamespace(log=SimpleNamespace(info=lambda *args: None))
    assert (
        merge_to_table_store(context, provider, config, [path], "main", "append")["rows_inserted"]
        == 1
    )
    assert written[0].to_pylist() == [{"id": 7}]
    assert "schema_policy" not in provider.ensure_table.call_args.kwargs


def test_invalid_schema_policy_fails_at_public_configuration():
    with pytest.raises(ValueError, match="Unknown schema policy"):
        phlo.ingest.dlt(
            table_name="events",
            unique_key="id",
            group="raw",
            table_schema=pa.schema([]),
            schema_policy="typo",
        )
