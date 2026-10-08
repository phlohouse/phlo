"""Exercise atomic merges through DLT ingestion and the public Iceberg resource.

Real DLT staging, SQLite catalogs and local data files run without services.
The catalog routing seam maps main and dev to isolated local catalogs.
"""

from typing import cast
from unittest.mock import MagicMock

import dlt
import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.table import Transaction
from pyiceberg.types import LongType, NestedField, StringType

from phlo.capabilities.interfaces import TableStore
from phlo.logging import get_logger
from phlo_dlt.executor import DltIngester
from phlo_dlt.registry import TableConfig
from phlo_iceberg.resource import IcebergResource


def test_ingestion_failure_replay_and_ref_isolation(tmp_path, monkeypatch):
    schema = Schema(
        NestedField(1, "id", LongType(), required=False),
        NestedField(2, "status", StringType(), required=False),
    )
    catalogs = {}
    for ref in ("main", "dev"):
        warehouse = tmp_path / ref
        warehouse.mkdir()
        catalogs[ref] = SqlCatalog(
            ref, uri=f"sqlite:///{tmp_path / (ref + '.db')}", warehouse=warehouse.as_uri()
        )
        catalogs[ref].create_namespace("raw")

    def catalog_for_ref(ref):
        return catalogs[ref]

    monkeypatch.setattr("phlo_iceberg.tables.get_catalog", catalog_for_ref)
    monkeypatch.setattr("phlo_iceberg.resource.get_catalog", catalog_for_ref)
    monkeypatch.setattr("phlo_iceberg.catalog.get_catalog", catalog_for_ref)

    def setup_pipeline(pipeline_name, dataset_name):
        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            destination=dlt.destinations.filesystem(bucket_url=str(tmp_path / "bucket")),
            dataset_name=dataset_name,
            pipelines_dir=str(tmp_path / "pipelines"),
        )
        return pipeline, tmp_path / "pipelines" / pipeline_name

    monkeypatch.setattr("phlo_dlt.executor.setup_dlt_pipeline", setup_pipeline)
    config = TableConfig(
        table_name="atomic_events",
        table_schema=schema,
        validation_schema=None,
        unique_key="id",
        group_name="raw",
        schema_policy="drop_extra",
    )
    main = catalogs["main"].create_table(config.full_table_name, schema)
    main.append(pa.Table.from_pylist([{"id": 7, "status": "main-only"}]))
    main_snapshot = main.current_snapshot().snapshot_id
    status = "old"

    def source(partition_date):
        yield from ({"id": i, "status": status} for i in range(1003))

    ingester = DltIngester(
        context=None,
        logger=get_logger("test_atomic_ingestion"),
        table_config=config,
        table_store_resource=cast(TableStore, IcebergResource(ref="main")),
        dlt_source_func=source,
        add_metadata_columns=True,
        merge_strategy="merge",
    )

    def run():
        return ingester.run_ingestion(partition_key="2026-10-07", parameters={"branch_name": "dev"})

    def dev_rows():
        return sorted(
            catalogs["dev"].load_table(config.full_table_name).scan().to_arrow().to_pylist(),
            key=lambda row: row["id"],
        )

    try:
        seeded = run()
        assert seeded.status == "success"
        assert seeded.rows_inserted == 1003
        assert len(seeded.metadata["parquet_paths"]) == 1
        before = dev_rows()
        assert before == [{"id": i, "status": "old"} for i in range(1003)]
        snapshot_before = catalogs["dev"].load_table(config.full_table_name).current_snapshot()
        status = "new"
        append = Transaction.append
        commit = MagicMock(wraps=catalogs["dev"].commit_table)

        def fail_after_append(self, *args, **kwargs):
            append(self, *args, **kwargs)
            raise RuntimeError("injected ingestion precommit failure")

        with monkeypatch.context() as failing:
            failing.setattr(Transaction, "append", fail_after_append)
            failing.setattr(catalogs["dev"], "commit_table", commit)
            with pytest.raises(RuntimeError, match="injected ingestion precommit failure"):
                run()
        assert commit.call_count == 0
        assert dev_rows() == before
        assert (
            catalogs["dev"].load_table(config.full_table_name).current_snapshot().snapshot_id
            == snapshot_before.snapshot_id
        )

        for _ in range(2):
            commit.reset_mock()
            with monkeypatch.context() as observing:
                observing.setattr(catalogs["dev"], "commit_table", commit)
                result = run()
            assert result.status == "success"
            assert result.rows_inserted == 1003
            assert result.rows_deleted == 1003
            assert commit.call_count == 1
            assert dev_rows() == [{"id": i, "status": "new"} for i in range(1003)]

        main_after = catalogs["main"].load_table(config.full_table_name)
        assert main_after.scan().to_arrow().to_pylist() == [{"id": 7, "status": "main-only"}]
        assert main_after.current_snapshot().snapshot_id == main_snapshot
    finally:
        for catalog in catalogs.values():
            catalog.engine.dispose()
