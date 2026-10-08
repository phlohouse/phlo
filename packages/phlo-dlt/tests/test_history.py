"""Exercise history configuration and the supported ingestion path.

Public decorated ingestion stages real DLT data and writes to a real catalog;
multi-file failures and operation evidence cover the provider boundary.
"""

from types import SimpleNamespace

import dlt
import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.memory import InMemoryCatalog
from pyiceberg.exceptions import CommitStateUnknownException
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType
import pytest

from phlo.capabilities.history import HistoryCommitUnknownError, HistoryConflictError
from phlo.capabilities.interfaces import TableStoreSupport
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo_dlt.decorator import (
    _default_merge_config,
    _validate_merge_config,
    clear_ingestion_assets,
    get_ingestion_assets,
    phlo_ingestion,
)
from phlo_dlt.dlt_helpers import merge_to_table_store
from phlo_dlt.executor import DltIngester
from phlo_dlt.registry import TableConfig
from phlo_iceberg import catalog as catalog_module
from phlo_iceberg import history, resource, tables
from phlo_iceberg.resource import IcebergResource

CONFIG = {"entity_key": "entity", "version_key": "version", "payload_columns": ["payload"]}
SCHEMA = pa.schema([("entity", pa.string()), ("version", pa.string()), ("payload", pa.string())])


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    catalog = InMemoryCatalog("test", warehouse=f"file://{tmp_path / 'warehouse'}")
    for module in (catalog_module, history, resource, tables):
        monkeypatch.setattr(module, "get_catalog", lambda **_kwargs: catalog)
    return catalog


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {**CONFIG, "entity_key": "version"},
        {**CONFIG, "payload_hash_column": "hash"},
        {**CONFIG, "payload_columns": []},
        {**CONFIG, "payload_columns": "payload"},
        {**CONFIG, "payload_columns": ["payload", "payload"]},
        {**CONFIG, "payload_columns": ["_phlo_ingested_at"]},
        {**CONFIG, "payload_columns": ["_dlt_id"]},
        {**CONFIG, "entity_key": None},
        {**CONFIG, "entity_key": ""},
        {**CONFIG, "version_key": "_phlo_row_id"},
        {**CONFIG, "deduplication": False},
        {**CONFIG, "deduplication_order_by": "time"},
        {"entity_key": "entity", "version_key": "version", "payload_hash_column": "_dlt_id"},
    ],
)
def test_history_config_rejects_invalid_or_ambiguous_policy(config):
    with pytest.raises(PhloConfigError):
        phlo_ingestion(
            "versions",
            "entity",
            "raw",
            table_schema=SCHEMA,
            merge_strategy="history",
            merge_config=config,
        )


@pytest.mark.parametrize(
    "config",
    [CONFIG, {"entity_key": "entity", "version_key": "version", "payload_hash_column": "hash"}],
)
def test_history_config_has_no_merge_deduplication_defaults(config):
    _validate_merge_config("history", "entity", config)
    assert _default_merge_config("history", config) == config


@pytest.mark.parametrize("advertised", [False, True])
def test_unsupported_provider_rejects_history_before_ensure_or_write(tmp_path, advertised):
    class UnsupportedStore:
        support = TableStoreSupport(supports_history=advertised)

        def ensure_table(self, **kwargs):
            raise AssertionError("unsupported history must fail before table creation")

    with pytest.raises(PhloConfigError, match="does not support atomic history"):
        merge_to_table_store(
            context=SimpleNamespace(log=get_logger("test-history")),
            table_store=UnsupportedStore(),
            table_config=TableConfig("versions", SCHEMA, None, "entity", "raw"),
            parquet_paths=[tmp_path / "unused.parquet"],
            branch_name="dev",
            merge_strategy="history",
            merge_config=CONFIG,
        )


def test_public_decorator_runs_real_dlt_staging_replay_and_late_versions(
    catalog, tmp_path, monkeypatch
):
    clear_ingestion_assets()
    store = IcebergResource(ref="dev")
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability", lambda _runtime: (store, "iceberg")
    )

    def setup(pipeline_name, dataset_name):
        return dlt.pipeline(
            pipeline_name=pipeline_name,
            dataset_name=dataset_name,
            destination=dlt.destinations.filesystem(bucket_url=str(tmp_path / "bucket")),
            pipelines_dir=str(tmp_path / "pipelines"),
        ), tmp_path / "pipelines" / pipeline_name

    monkeypatch.setattr("phlo_dlt.executor.setup_dlt_pipeline", setup)
    rows = [{"entity": "E1", "version": "V1", "payload": "original"}]
    events = []
    monkeypatch.setattr("phlo_dlt.executor.emit_observation", lambda **event: events.append(event))

    @phlo_ingestion(
        "versions",
        "entity",
        "raw",
        table_schema=Schema(
            NestedField(1, "entity", StringType(), required=False),
            NestedField(2, "version", StringType(), required=False),
            NestedField(3, "payload", StringType(), required=False),
            NestedField(4, "_dlt_id", StringType(), required=True),
            NestedField(5, "_dlt_load_id", StringType(), required=True),
        ),
        validate=False,
        strict_validation=False,
        merge_strategy="history",
        merge_config=CONFIG,
        schema_policy="additive",
        partitioned=False,
    )
    def versions(partition_date: str):
        return dlt.resource(rows, name="versions", write_disposition="append")

    runtime = SimpleNamespace(
        run_id=f"{tmp_path}-first",
        tags={"phlo/ref": "dev", "phlo/project_id": "project", "phlo/attempt": "1"},
        resources={},
        logger=get_logger("history-public"),
    )
    asset = get_ingestion_assets()[0]
    try:
        first = list(asset.run.fn(runtime))[-1]
        assert first.metadata["rows_inserted"] == 1
        before = catalog.load_table("raw.versions").metadata.model_dump()
        runtime.run_id = f"{tmp_path}-replay"
        replay = list(asset.run.fn(runtime))[-1]
        assert replay.metadata["rows_inserted"] == 0
        assert replay.metadata["rows_skipped"] == 1
        assert replay.metadata["rows_conflicting"] == 0
        assert catalog.load_table("raw.versions").metadata.model_dump() == before
        rows[:] = [{"entity": "E1", "version": "V0", "payload": "late"}]
        runtime.run_id = f"{tmp_path}-late"
        late = list(asset.run.fn(runtime))[-1]
        assert late.metadata["rows_inserted"] == 1
        stored = catalog.load_table("raw.versions").scan().to_arrow().to_pylist()
        assert {(row["version"], row["payload"]) for row in stored} == {
            ("V1", "original"),
            ("V0", "late"),
        }
        assert all(row["_phlo_ingested_at"] is not None for row in stored)
        assert events[1]["metrics"]["rows_skipped"] == 1
        assert events[1]["metrics"]["rows_inserted"] == 0
    finally:
        clear_ingestion_assets()


@pytest.mark.parametrize("unknown", [False, True])
def test_multifile_executor_preserves_counts_and_reconciliation_evidence(
    catalog, tmp_path, monkeypatch, unknown
):
    left, right = tmp_path / "left.parquet", tmp_path / "right.parquet"
    pq.write_table(
        pa.Table.from_pylist([{"entity": "E1", "version": "V1", "payload": "a"}], schema=SCHEMA),
        left,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [{"entity": "E1", "version": "V1", "payload": "a" if unknown else "b"}], schema=SCHEMA
        ),
        right,
    )
    monkeypatch.setattr(
        "phlo_dlt.executor.setup_dlt_pipeline", lambda **kw: (SimpleNamespace(), tmp_path)
    )
    monkeypatch.setattr("phlo_dlt.executor.stage_to_parquet", lambda **kw: ([left, right], 0.01))
    captured = []
    monkeypatch.setattr(
        "phlo_dlt.executor.emit_observation", lambda **event: captured.append(event)
    )
    if unknown:
        commit = catalog.commit_table

        def lost(table, requirements, updates):
            commit(table, requirements, updates)
            raise CommitStateUnknownException("lost response")

        monkeypatch.setattr(catalog, "commit_table", lost)
    ingester = DltIngester(
        context=SimpleNamespace(
            run_id="run", tags={"phlo/project_id": "project", "phlo/attempt": "1"}
        ),
        logger=get_logger("history-executor"),
        table_config=TableConfig("versions", SCHEMA, None, "entity", "raw"),
        table_store_resource=IcebergResource(ref="dev"),
        dlt_source_func=lambda partition_date: object(),
        validate=False,
        add_metadata_columns=False,
        merge_strategy="history",
        merge_config=CONFIG,
    )
    with pytest.raises(HistoryCommitUnknownError if unknown else HistoryConflictError):
        ingester.run_ingestion(
            "2026-10-07",
            parameters={
                "branch_name": "dev",
                "run_id": str(tmp_path),
                "project_id": "project",
                "attempt": 1,
            },
        )
    assert captured[0]["status"] == "failed"
    output = next(item for item in captured[0]["resources"] if item["role"] == "output")
    if unknown:
        assert captured[0]["metrics"] == {}
        assert output["metadata"]["outcome"] == "unknown"
        assert output["metadata"]["reconciliation"]["versions_present"] == 1
        assert output["metadata"]["reconciliation"]["versions_missing"] == 0
        assert output["metadata"]["reconciliation"]["versions_conflicting"] == 0
        assert len(catalog.load_table("raw.versions").scan().to_arrow()) == 1
    else:
        assert captured[0]["metrics"] == {
            "rows_inserted": 0,
            "rows_deleted": 0,
            "rows_skipped": 0,
            "rows_conflicting": 2,
        }
        assert output["metadata"]["outcome"] == "failed"
        table = catalog.load_table("raw.versions")
        assert table.current_snapshot() is None
        assert history.POLICY_PROPERTY not in table.properties
