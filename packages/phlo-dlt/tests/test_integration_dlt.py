"""Integration tests for phlo-dlt using shared infrastructure.

Runs real ingestion through dlt -> parquet -> pyiceberg against the shared
MinIO-backed Iceberg catalog fixture, with the catalog getter patched to a
test-scoped catalog.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from pandera.pandas import DataFrameModel
from typing import cast

pytest.importorskip("pyiceberg")

from phlo.capabilities.interfaces import TableStore
from phlo.logging import get_logger
from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets, phlo_ingestion
from phlo_dlt.registry import TableConfig
from phlo_iceberg.resource import IcebergResource
# from phlo_dlt.decorator import phlo_ingestion # Removed to avoid Dagster dependency in this test

pytestmark = pytest.mark.integration


class MySchema(DataFrameModel):
    """Test schema for integration ingestion rows."""

    id: int
    name: str


def test_phlo_ingestion_execution_real(tmp_path, iceberg_catalog):
    """
    Test executing the created asset using a REAL local Iceberg catalog (backed by MinIO/Sqlite).
    This verifies the full flow: dlt -> parquet -> pyiceberg -> minio.
    """

    # 1. Define the asset
    # 1. Define the source (Just a plain python function now!)
    def my_source(partition_date: str):
        """Yield fixture rows for the given partition date."""

        yield {"id": 1, "name": "foo"}
        yield {"id": 2, "name": "bar"}

    # 2. Setup Resources
    # We use the real IcebergResource, but we patch the catalog getter to return our
    # test-scoped fixture catalog (which points to MinIO).

    iceberg_resource = IcebergResource()

    # 3. Use the executor directly (orchestrator-agnostic). Each module
    # (resource, tables, catalog) imports get_catalog separately, so every
    # binding must be patched for the fixture catalog to take effect.
    with (
        patch("phlo_iceberg.resource.get_catalog", return_value=iceberg_catalog),
        patch("phlo_iceberg.tables.get_catalog", return_value=iceberg_catalog),
        patch("phlo_iceberg.catalog.get_catalog", return_value=iceberg_catalog),
    ):
        # Core logic setup
        table_name = "real_integration_test"
        table_config = TableConfig(
            table_name=table_name,
            table_schema=None,
            validation_schema=MySchema,
            unique_key="id",
            group_name="integration_test",
        )

        # Initialize the Executor
        # We pass a dummy logger or use standard logging
        logger = get_logger("test_logger")

        from phlo_dlt.executor import DltIngester

        ingester = DltIngester(
            context=None,  # Ingester handles context=None gracefully or we mock it if needed
            logger=logger,
            table_config=table_config,
            table_store_resource=cast(TableStore, iceberg_resource),
            dlt_source_func=my_source,
            add_metadata_columns=True,
            merge_strategy="merge",
        )

        # 4. Execute Logic
        partition_key = "2025-01-02"
        result = ingester.run_ingestion(partition_key=partition_key)

        assert result.status == "success"
        assert result.rows_inserted == 2
        assert len(result.metadata["parquet_paths"]) == 1
        assert result.metadata["parquet_path"] == result.metadata["parquet_paths"][0]

        # 5. Verify Iceberg Table Content
        table = iceberg_catalog.load_table(table_config.full_table_name)
        df = table.scan().to_arrow().to_pylist()

        assert len(df) == 2
        # Sort by id to ensure deterministic check
        df.sort(key=lambda x: x["id"])
        assert df[0]["id"] == 1
        assert df[0]["name"] == "foo"
        assert df[1]["id"] == 2
        assert df[1]["name"] == "bar"

        # Verify metadata injection
        assert "_phlo_run_id" in df[0]
        assert df[0]["_phlo_partition_date"] == "2025-01-02"


def test_strict_domain_quality_failure_leaves_real_iceberg_main_unchanged(
    tmp_path, iceberg_catalog, monkeypatch
):
    """A strict decorated rejection must not change the real fixture's main table."""
    table_name = f"strict_quality_{uuid4().hex[:12]}"
    table_config = TableConfig(
        table_name=table_name,
        table_schema=None,
        validation_schema=MySchema,
        unique_key="id",
        group_name="integration_test",
    )
    iceberg_resource = IcebergResource()

    # This fixture uses a real MinIO-or-local-filesystem Iceberg warehouse with
    # a SQLite catalog. It has no named-ref/WAP implementation, so the test
    # proves the strongest available invariant: a rejected run never writes to
    # the actual catalog even when its routed WAP ref resolves to that catalog.
    with (
        patch("phlo_iceberg.resource.get_catalog", return_value=iceberg_catalog),
        patch("phlo_iceberg.tables.get_catalog", return_value=iceberg_catalog),
        patch("phlo_iceberg.catalog.get_catalog", return_value=iceberg_catalog),
    ):
        monkeypatch.setattr(
            "phlo_dlt.decorator._resolve_table_store_capability",
            lambda _runtime: (iceberg_resource, "iceberg"),
        )
        clear_ingestion_assets()

        @phlo_ingestion(
            table_name=table_name,
            unique_key="id",
            group="integration_test",
            validation_schema=MySchema,
        )
        def seed_source(partition_date: str):
            yield {"id": 1, "name": "seed"}

        seed_runtime = SimpleNamespace(
            run_id="strict-quality-real-iceberg-seed",
            partition_key="2026-08-28",
            tags={"phlo/ref": "main"},
            resources={},
            logger=get_logger("test_strict_domain_quality_real_iceberg"),
        )
        list(get_ingestion_assets()[0].run.fn(seed_runtime))
        main_before = iceberg_catalog.load_table(table_config.full_table_name)
        rows_before = main_before.scan().to_arrow().to_pylist()
        snapshot_before = main_before.current_snapshot().snapshot_id
        clear_ingestion_assets()

        @phlo_ingestion(
            table_name=table_name,
            unique_key="id",
            group="integration_test",
            validation_schema=MySchema,
            quality_checks=[lambda frame: "id 2 is rejected" if 2 in frame["id"].values else None],
        )
        def rejected_source(partition_date: str):
            yield {"id": 2, "name": "rejected"}

        runtime = SimpleNamespace(
            run_id="strict-quality-real-iceberg",
            partition_key="2026-08-29",
            tags={"phlo/ref": "main", "phlo/wap_branch": "wap-strict-quality"},
            resources={},
            logger=get_logger("test_strict_domain_quality_real_iceberg"),
        )
        try:
            with pytest.raises(RuntimeError, match="Domain quality check failed"):
                list(get_ingestion_assets()[0].run.fn(runtime))

            main_after = iceberg_catalog.load_table(table_config.full_table_name)
            assert main_after.scan().to_arrow().to_pylist() == rows_before
            assert main_after.current_snapshot().snapshot_id == snapshot_before
        finally:
            clear_ingestion_assets()
            iceberg_catalog.drop_table(table_config.full_table_name)
