"""Real bounded DLT extraction and Iceberg upserts with persistent cursor controls."""

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import dlt
import pyarrow.parquet as pq
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, TimestamptzType

from phlo.capabilities import DataInterval, RuntimeRouting
from phlo.hooks import get_hook_bus
from phlo.logging import get_logger
from phlo.run_evidence import CoreRunEvidenceHookProvider, SQLiteRunEvidenceStore
from phlo_dlt.decorator import phlo_ingestion
from phlo_dlt.executor import DltIngester
from phlo_dlt.registry import TableConfig
from phlo_iceberg.resource import IcebergResource


def test_intervals_preserve_cursor_across_replay_and_uncertain_writes(tmp_path, monkeypatch):
    """No cursor/table transaction is implied, even when a committed write loses its reply."""
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    catalog = SqlCatalog(
        "intervals", uri=f"sqlite:///{tmp_path / 'catalog.db'}", warehouse=warehouse.as_uri()
    )
    catalog.create_namespace("raw")
    # Only connection wiring is substituted. All extraction, Parquet, table
    # schema alignment, merge transactions, catalog commits and reads are real.
    monkeypatch.setattr("phlo_iceberg.resource.get_catalog", lambda **kw: catalog)
    monkeypatch.setattr("phlo_iceberg.tables.get_catalog", lambda **kw: catalog)
    monkeypatch.setattr("phlo_iceberg.catalog.get_catalog", lambda **kw: catalog)
    start = datetime(2026, 3, 29, tzinfo=timezone.utc)
    rows = [
        {"id": 1, "updated_at": start},
        {"id": 2, "updated_at": start + timedelta(hours=23)},
        {"id": 3, "updated_at": start + timedelta(hours=47)},
    ]
    pipeline = dlt.pipeline(
        pipeline_name="interval_proof",
        pipelines_dir=str(tmp_path / "pipelines"),
        destination=dlt.destinations.filesystem(bucket_url=str(tmp_path / "staging")),
        dataset_name="interval_proof",
    )

    def resource():
        return dlt.resource(rows, name="orders", primary_key="id")

    live = resource()
    live.apply_hints(incremental=dlt.sources.incremental("updated_at", initial_value=start))
    pipeline.run(live, loader_file_format="parquet")
    cursor_before = deepcopy(pipeline.state["sources"])
    monkeypatch.setattr("phlo_dlt.executor.setup_dlt_pipeline", lambda **kw: (pipeline, tmp_path))
    config = TableConfig(
        table_name="orders",
        group_name="raw",
        namespace="raw",
        unique_key="id",
        validation_schema=None,
        schema_policy="drop_extra",  # DLT staging metadata is not part of the destination schema.
        table_schema=Schema(
            NestedField(1, "id", LongType(), required=False),
            NestedField(2, "updated_at", TimestamptzType(), required=False),
        ),
    )
    bounds = []

    def source(data_interval):
        bounds.append(data_interval)
        return resource()

    store = IcebergResource(ref="main")

    def run(begin, end, mode="scheduled", parameters=None):
        context = SimpleNamespace(
            routing=RuntimeRouting(data_interval=DataInterval(begin, end), interval_mode=mode)
        )
        return DltIngester(
            context=context,
            logger=get_logger("interval_proof"),
            table_config=config,
            table_store_resource=store,
            dlt_source_func=source,
            validate=False,
            add_metadata_columns=False,
            interval_column="updated_at",
            interval_lookback=timedelta(hours=2),
        ).run_ingestion("2026-03-29", parameters=parameters)

    def ids():
        return sorted(
            row["id"] for row in catalog.load_table("raw.orders").scan().to_arrow().to_pylist()
        )

    try:
        first = run(start, start + timedelta(hours=23))
        assert first.status == "success"
        assert [
            row["id"] for row in pq.read_table(first.metadata["parquet_paths"][0]).to_pylist()
        ] == [1]
        assert ids() == [1]  # End-boundary record belongs to the next interval.
        assert pipeline.state["sources"] == cursor_before
        rows.append({"id": 4, "updated_at": start + timedelta(hours=22)})
        assert run(start + timedelta(hours=23), start + timedelta(hours=47)).status == "success"
        assert ids() == [1, 2, 4]  # Lookback recovers the late row, not the upper boundary.
        assert bounds[-1].start == start + timedelta(hours=21)
        assert pipeline.state["sources"] == cursor_before
        assert run(start, start + timedelta(hours=23), "replay").status == "success"
        assert bounds[-1].start == start  # Replay does not silently widen the requested window.
        assert ids() == [1, 2, 4]
        assert pipeline.state["sources"] == cursor_before

        merge = store.merge_parquet

        def failed_write(**kwargs):
            raise RuntimeError("rejected before commit")

        monkeypatch.setattr(store, "merge_parquet", failed_write)
        with pytest.raises(RuntimeError, match="rejected before commit"):
            run(start + timedelta(hours=47), start + timedelta(hours=71))
        assert ids() == [1, 2, 4]
        assert pipeline.state["sources"] == cursor_before

        def unknown_write(**kwargs):
            merge(**kwargs)
            raise RuntimeError("reply lost after commit")

        monkeypatch.setattr(store, "merge_parquet", unknown_write)
        with pytest.raises(RuntimeError, match="reply lost after commit"):
            run(start + timedelta(hours=47), start + timedelta(hours=71))
        assert ids() == [1, 2, 3, 4]
        assert pipeline.state["sources"] == cursor_before
        monkeypatch.setattr(store, "merge_parquet", merge)
        assert run(start + timedelta(hours=47), start + timedelta(hours=71)).status == "success"
        assert ids() == [1, 2, 3, 4]
        assert pipeline.state["sources"] == cursor_before
        evidence_path = tmp_path / "empty-window.sqlite"
        with closing(SQLiteRunEvidenceStore(evidence_path)) as evidence:
            bus = get_hook_bus()
            bus.clear()
            bus.register_provider(CoreRunEvidenceHookProvider(evidence), plugin_name="test")
            try:
                assert (
                    run(
                        start + timedelta(days=10),
                        start + timedelta(days=11),
                        parameters={
                            "project_id": "interval-proof",
                            "run_id": "empty-window",
                            "attempt": 2,
                        },
                    ).status
                    == "no_data"
                )
            finally:
                bus.clear()
        with closing(SQLiteRunEvidenceStore(evidence_path)) as reopened:
            run_evidence = reopened.get_run("interval-proof", "empty-window")
            assert run_evidence["status"] == "no_data"
            resources = reopened.list_resources("interval-proof", "empty-window", attempt=2)
            assert len(resources) == 1
            assert resources[0]["role"] == "input"
            assert resources[0]["metadata"]["records_read"] == {"status": "observed", "value": 0}
        assert ids() == [1, 2, 3, 4]
        assert pipeline.state["sources"] == cursor_before
        pipeline = dlt.attach(pipeline.pipeline_name, pipelines_dir=pipeline.pipelines_dir)
        assert (
            pipeline.state["sources"] == cursor_before
        )  # Read persisted state, not a cached value.
    finally:
        catalog.engine.dispose()


@pytest.mark.parametrize(
    "options",
    [
        {"interval_column": "updated_at", "merge_strategy": "append"},
        {"interval_column": ""},
        {"interval_column": "updated_at", "unique_key": ""},
        {"interval_lookback": timedelta(hours=1)},
        {"interval_column": "updated_at", "interval_lookback": timedelta(hours=-1)},
    ],
)
def test_interval_configuration_rejected_at_authoring(options):
    with pytest.raises(ValueError, match="Interval"):
        phlo_ingestion(table_name="bad", group="raw", **{"unique_key": "id", **options})
