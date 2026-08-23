"""Integration tests for phlo-dlt using shared infrastructure.

Runs real ingestion through dlt -> parquet -> pyiceberg against the shared
MinIO-backed Iceberg catalog fixture, with the catalog getter patched to a
test-scoped catalog.
"""

from unittest.mock import patch

import pytest
from pandera.pandas import DataFrameModel
from typing import cast

pytest.importorskip("pyiceberg")

from phlo.capabilities.interfaces import TableStore
from phlo.logging import get_logger
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
