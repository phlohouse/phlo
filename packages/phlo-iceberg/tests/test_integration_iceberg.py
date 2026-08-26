"""Live-service integration tests for phlo-iceberg.

The only coverage retained here exercises a real Nessie/MinIO catalog through
the production settings path. Everything that ran against mocked catalogs was
consolidated into test_iceberg.py or superseded by the contract modules
(retention, compaction, object inventory, evidence, rollback).
"""

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType

pytestmark = pytest.mark.integration


@pytest.fixture
def iceberg_catalog(configured_minio_object_store, monkeypatch):
    """Fixture providing a real Iceberg catalog for testing."""
    try:
        from phlo.capabilities import resolve_capability
        from phlo_iceberg.catalog import get_catalog, reset_catalog_cache
        from phlo_iceberg.settings import get_settings as get_iceberg_settings
        from phlo_minio.settings import get_settings as get_minio_settings
        from phlo_nessie.settings import get_settings as get_nessie_settings

        object_store = (
            resolve_capability("object_store", "minio") if configured_minio_object_store else None
        )
        if object_store:
            config = object_store.provider.to_sling_connection()
            endpoint = config["endpoint"]
            monkeypatch.setenv("ICEBERG_S3_ENDPOINT", endpoint)
            monkeypatch.setenv("ICEBERG_S3_ACCESS_KEY", config["access_key_id"])
            monkeypatch.setenv("ICEBERG_S3_SECRET_KEY", config["secret_access_key"])
            monkeypatch.setenv("ICEBERG_S3_REGION", config["region"])
        else:
            minio_host = os.environ.get("MINIO_HOST", "127.0.0.1")
            minio_port = os.environ.get("MINIO_API_PORT", "10001")
            endpoint = f"http://{minio_host}:{minio_port}"
            monkeypatch.setenv("ICEBERG_S3_ENDPOINT", endpoint)
            monkeypatch.setenv("ICEBERG_S3_ACCESS_KEY", os.environ.get("MINIO_ROOT_USER", "minio"))
            monkeypatch.setenv(
                "ICEBERG_S3_SECRET_KEY", os.environ.get("MINIO_ROOT_PASSWORD", "minio123")
            )
            monkeypatch.setenv("ICEBERG_S3_REGION", "us-east-1")

        parsed_endpoint = urlparse(endpoint)
        endpoint_host = parsed_endpoint.hostname or "127.0.0.1"
        endpoint_port = parsed_endpoint.port or 80

        try:
            with socket.create_connection((endpoint_host, endpoint_port), timeout=1):
                pass
        except OSError:
            pytest.skip(f"MinIO endpoint not reachable at {endpoint}")

        # Settings and catalogs are lru_cached and derived from the
        # environment, so clear them after setting env vars or the fixture's
        # credentials never reach the catalog. Reset again on teardown to
        # avoid leaking a cached catalog into unrelated tests.
        reset_catalog_cache()
        get_iceberg_settings.cache_clear()
        get_minio_settings.cache_clear()
        get_nessie_settings.cache_clear()
        catalog = get_catalog()
        yield catalog
    except Exception as e:
        pytest.skip(f"Iceberg catalog not available: {e}")
    finally:
        try:
            from phlo_iceberg.catalog import reset_catalog_cache
            from phlo_iceberg.settings import get_settings as get_iceberg_settings
            from phlo_minio.settings import get_settings as get_minio_settings
            from phlo_nessie.settings import get_settings as get_nessie_settings

            reset_catalog_cache()
            get_iceberg_settings.cache_clear()
            get_minio_settings.cache_clear()
            get_nessie_settings.cache_clear()
        except Exception:
            pass


class TestIcebergIntegrationReal:
    """Real integration tests with an Iceberg catalog."""

    def test_create_and_drop_table(self, iceberg_catalog):
        """Test creating and dropping a table."""
        from phlo_iceberg import ensure_table
        import uuid

        table_name = f"test_ns.test_table_{uuid.uuid4().hex[:8]}"

        schema = Schema(
            NestedField(1, "id", LongType(), required=True),
            NestedField(2, "name", StringType(), required=True),
        )

        try:
            table = ensure_table(table_name, schema)
            assert table is not None

            # Verify table exists
            loaded = iceberg_catalog.load_table(table_name)
            assert loaded is not None
        finally:
            # Cleanup
            try:
                iceberg_catalog.drop_table(table_name)
            except Exception:
                pass

    def test_append_and_read_data(self, iceberg_catalog, tmp_path):
        """Test appending data and reading it back."""
        from phlo_iceberg import ensure_table, append_to_table
        import uuid

        table_name = f"test_ns.append_test_{uuid.uuid4().hex[:8]}"

        schema = Schema(
            NestedField(1, "id", LongType(), required=True),
            NestedField(2, "value", StringType(), required=True),
        )

        try:
            # Create table
            ensure_table(table_name, schema)

            # Write test data
            parquet_path = Path(tmp_path) / "data.parquet"
            df = pd.DataFrame({"id": [1, 2, 3], "value": ["a", "b", "c"]})
            df.to_parquet(parquet_path)

            result = append_to_table(table_name, parquet_path)
            assert result["rows_inserted"] == 3

            # Read back
            table = iceberg_catalog.load_table(table_name)
            scan = table.scan()
            read_df = scan.to_pandas()

            assert len(read_df) == 3
            assert set(read_df["value"].tolist()) == {"a", "b", "c"}
        finally:
            try:
                iceberg_catalog.drop_table(table_name)
            except Exception:
                pass
