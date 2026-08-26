"""
Tests for the Phlo testing infrastructure.

Validates that all mock resources work correctly and can be used for testing assets.
"""

from pathlib import Path

import pandas as pd
import pytest

from phlo_testing import (
    FixtureRecorder,
    MockIcebergCatalog,
    MockTrinoResource,
    local_test_mode,
    mock_dlt_source,
    mock_dlt_source_with_error,
)
from phlo_testing import (
    test_asset_execution as run_asset_test,  # Aliased to avoid pytest collection
)
from phlo_testing.placeholders import (
    MockIcebergCatalog as PlaceholderMockIcebergCatalog,
    mock_iceberg_catalog as placeholder_mock_iceberg_catalog,
)
from phlo_testing.mock_iceberg import MockIcebergCatalog as CanonicalMockIcebergCatalog

# ========== MockIcebergCatalog Tests ==========


class TestMockIcebergCatalog:
    """Test MockIcebergCatalog functionality."""

    def test_placeholder_catalog_aliases_canonical_implementation(self):
        """Placeholder import path should resolve to the canonical catalog."""
        assert PlaceholderMockIcebergCatalog is CanonicalMockIcebergCatalog

    def test_placeholder_helper_returns_canonical_catalog(self):
        """Placeholder helper should construct the canonical catalog implementation."""
        with placeholder_mock_iceberg_catalog() as catalog:
            assert isinstance(catalog, CanonicalMockIcebergCatalog)

    def test_create_table(self):
        """Test creating a table."""
        catalog = MockIcebergCatalog()

        # Simple dict-based schema (doesn't require PyIceberg)
        schema = {"id": "int", "name": "string"}

        table = catalog.create_table("raw.users", schema=schema)
        assert table is not None
        assert table.name == "raw.users"

    def test_append_data(self):
        """Test appending data to table."""
        catalog = MockIcebergCatalog()

        schema = {"id": "int", "name": "string"}

        table = catalog.create_table("raw.users", schema=schema)

        df = pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})
        table.append(df)

        result = table.scan().to_pandas()
        assert len(result) == 3
        assert list(result.columns) == ["id", "name"]

    def test_overwrite_data(self):
        """Test overwriting table data."""
        catalog = MockIcebergCatalog()

        schema = {"id": "int", "name": "string"}

        table = catalog.create_table("raw.users", schema=schema)

        df1 = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
        table.append(df1)

        df2 = pd.DataFrame({"id": [3, 4, 5], "name": ["Charlie", "Dave", "Eve"]})
        table.overwrite(df2)

        result = table.scan().to_pandas()
        assert len(result) == 3
        assert list(result["id"]) == [3, 4, 5]

    def test_list_tables(self):
        """Test listing tables in namespace."""
        catalog = MockIcebergCatalog()

        schema = {"id": "int"}

        catalog.create_table("raw.users", schema=schema)
        catalog.create_table("raw.orders", schema=schema)

        tables = catalog.list_tables("raw")
        assert len(tables) == 2
        assert "raw.users" in tables
        assert "raw.orders" in tables

    def test_drop_table(self):
        """Test dropping a table."""
        catalog = MockIcebergCatalog()

        schema = {"id": "int"}

        catalog.create_table("raw.test", schema=schema)
        assert catalog.table_exists("raw.test")

        catalog.drop_table("raw.test")
        assert not catalog.table_exists("raw.test")

    def test_context_manager(self):
        """Test using catalog as context manager."""
        with MockIcebergCatalog() as catalog:
            assert catalog is not None
            assert len(catalog.list_namespaces()) > 0

    def test_schema_validation(self):
        """Test schema validation on append."""
        catalog = MockIcebergCatalog()

        schema = {"id": "int", "name": "string"}

        table = catalog.create_table("raw.users", schema=schema)

        # Wrong columns should raise error
        df_bad = pd.DataFrame({"id": [1], "age": [30]})

        with pytest.raises(ValueError, match="Schema mismatch"):
            table.append(df_bad)

    def test_placeholders_import_uses_canonical_catalog(self):
        """Legacy placeholders import exposes the canonical MockIcebergCatalog."""
        assert PlaceholderMockIcebergCatalog is CanonicalMockIcebergCatalog

    def test_create_table_if_not_exists_returns_existing_table(self):
        """Mock catalog keeps placeholder-compatible idempotent table creation."""
        catalog = MockIcebergCatalog()
        schema = {"id": "int"}

        first = catalog.create_table("raw.users", schema=schema, if_not_exists=True)
        second = catalog.create_table("raw.users", schema=schema, if_not_exists=True)

        assert second is first
        assert catalog.list_tables() == ["raw.users"]


# ========== MockDLTSource Tests ==========


class TestMockDLTSource:
    """Test mock DLT sources."""

    def test_single_resource(self):
        """Test single resource source."""
        data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

        source = mock_dlt_source(data, resource_name="users")

        records = list(source)
        assert len(records) == 2
        assert records[0]["name"] == "Alice"

    def test_multiple_iterations(self):
        """Test iterating source multiple times."""
        data = [{"id": 1}, {"id": 2}]
        source = mock_dlt_source(data, resource_name="test")

        # First iteration
        records1 = list(source)
        assert len(records1) == 2

        # Second iteration should also work (reset)
        records2 = list(source)
        assert len(records2) == 2

    def test_error_injection(self):
        """Test error injection in source."""
        data = [{"id": 1}, {"id": 2}, {"id": 3}]

        source = mock_dlt_source_with_error(
            data,
            error_after=2,
            error_message="API limit exceeded",
        )

        with pytest.raises(Exception, match="API limit exceeded"):
            list(source)


# ========== MockTrinoResource Tests ==========


class TestMockTrinoResource:
    """Test MockTrinoResource functionality."""

    def test_execute_query(self):
        """Test executing a query."""
        trino = MockTrinoResource()

        cursor = trino.cursor()
        cursor.execute("SELECT 1 as id, 'test' as name")

        results = cursor.fetchall()
        assert len(results) == 1
        assert results[0][0] == 1

    def test_create_table(self):
        """Test creating a table."""
        trino = MockTrinoResource()

        cursor = trino.cursor()
        cursor.execute("CREATE TABLE test AS SELECT 1 as id, 'Alice' as name")

        # Query the table
        cursor.execute("SELECT * FROM test")
        results = cursor.fetchall()
        assert len(results) == 1

    def test_load_dataframe(self):
        """Test loading a DataFrame as a table."""
        trino = MockTrinoResource()

        df = pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})
        trino.load_table("test.users", df)

        # Query the loaded table
        cursor = trino.cursor()
        cursor.execute("SELECT * FROM test_users")
        results = cursor.fetchall()
        assert len(results) == 3

    def test_context_manager(self):
        """Test using resource as context manager."""
        with MockTrinoResource() as trino:
            cursor = trino.cursor()
            cursor.execute("SELECT 1 as id")
            results = cursor.fetchall()
            assert len(results) == 1

    def test_connection_context(self):
        """Test connection context manager."""
        trino = MockTrinoResource()

        with trino.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 as id")
            results = cursor.fetchall()
            assert len(results) == 1


# ========== Asset Execution Tests ==========


class TestAssetExecution:
    """Test asset execution with mocks."""

    def test_simple_asset(self):
        """Test executing a simple asset."""

        def simple_asset(partition_date: str):
            """Return one synthetic record for the requested partition."""
            return [{"id": 1, "date": partition_date}]

        result = run_asset_test(simple_asset, partition="2024-01-01")

        assert result.success
        assert result.data is not None
        assert len(result.data) == 1
        assert result.data.iloc[0]["id"] == 1

    def test_dataframe_asset(self):
        """Test asset that returns DataFrame."""

        def dataframe_asset(partition_date: str):
            """Return a synthetic DataFrame payload."""
            return pd.DataFrame(
                {
                    "id": [1, 2, 3],
                    "value": [10, 20, 30],
                }
            )

        result = run_asset_test(dataframe_asset, partition="2024-01-01")

        assert result.success
        assert result.data is not None
        assert len(result.data) == 3
        assert result.metadata["row_count"] == 3

    def test_asset_with_mock_data(self):
        """Test asset with provided mock data."""

        def ingestion_asset(partition_date: str):
            """Return one ingestion-style record."""
            return [{"id": 1, "name": "Test", "date": partition_date}]

        mock_data = [{"id": 1, "name": "Test"}]

        result = run_asset_test(
            ingestion_asset,
            partition="2024-01-15",
            mock_data=mock_data,
        )

        assert result.success
        assert result.data is not None
        assert len(result.data) == 1

    def test_asset_execution_time(self):
        """Test that execution time is captured."""

        def quick_asset(partition_date: str):
            """Return one record quickly for timing assertions."""
            return [{"id": 1}]

        result = run_asset_test(quick_asset, partition="2024-01-01")

        assert result.duration > 0
        assert result.duration < 5  # Should be fast

    def test_failed_asset(self):
        """Test handling of failed asset."""

        def failing_asset(partition_date: str):
            """Raise an error to simulate asset failure.

            Raises: ValueError when always raised for failure-path assertions.
            """
            raise ValueError("Asset failed")

        result = run_asset_test(failing_asset, partition="2024-01-01")

        assert not result.success
        assert result.error is not None
        assert isinstance(result.error, ValueError)

    def test_empty_result(self):
        """Test asset that returns no data."""

        def empty_asset(partition_date: str):
            """Return an empty payload."""
            return []

        result = run_asset_test(empty_asset, partition="2024-01-01")

        assert result.success
        assert result.data is not None
        assert len(result.data) == 0


# ========== Local Test Mode Tests ==========


class TestLocalTestMode:
    """Test local test mode functionality."""

    def test_context_manager(self):
        """Test local mode as context manager."""

        with local_test_mode() as mode:
            assert mode.table_store is not None
            assert mode.trino is not None

    def test_fixture_recording(self, tmp_path):
        """Test recording and loading fixtures."""

        fixture_dir = Path(tmp_path)

        with local_test_mode(fixture_dir=fixture_dir) as mode:
            data = {"id": 1, "name": "Alice"}
            mode.record_fixture("test_data", data)

        # Load fixture
        with local_test_mode(fixture_dir=fixture_dir) as mode:
            loaded = mode.load_fixture("test_data")
            assert loaded["id"] == 1

    def test_get_resource(self):
        """Test getting resources from local mode."""

        with local_test_mode() as mode:
            table_store = mode.get_resource("table_store")
            trino = mode.get_resource("trino")

            assert isinstance(table_store, MockIcebergCatalog)
            assert isinstance(trino, MockTrinoResource)

    def test_invalid_resource(self):
        """Test getting invalid resource."""

        with local_test_mode() as mode:
            with pytest.raises(ValueError, match="Unknown resource"):
                mode.get_resource("invalid")


# ========== FixtureRecorder Tests ==========


class TestFixtureRecorder:
    """Test fixture recording functionality."""

    def test_record_dlt_source(self, tmp_path):
        """Test recording DLT source data."""

        recorder = FixtureRecorder(fixture_dir=Path(tmp_path))

        # Record a DLT source
        def fake_source():
            """Yield fixture rows for recording tests."""
            yield {"id": 1, "name": "Alice"}
            yield {"id": 2, "name": "Bob"}

        data = recorder.record_dlt_source("users", fake_source)

        assert len(data) == 2
        assert data[0]["name"] == "Alice"

        # Fixture should be saved
        fixtures = recorder.list_fixtures()
        assert "users_dlt" in fixtures

    def test_load_dlt_fixture(self, tmp_path):
        """Test loading recorded DLT fixture."""

        recorder = FixtureRecorder(fixture_dir=Path(tmp_path))

        # Record fixture
        def fake_source():
            """Yield one fixture row for load tests."""
            yield {"id": 1, "value": 42}

        recorder.record_dlt_source("test", fake_source)

        # Load it back
        source = recorder.load_dlt_fixture("test")
        data = list(source)

        assert len(data) == 1
        assert data[0]["value"] == 42


# ========== Integration Tests ==========


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_workflow(self):
        """Test complete workflow with all mocks."""
        # Create catalog
        catalog = MockIcebergCatalog()

        # Create schema
        schema = {"id": "int", "name": "string"}

        # Create table
        table = catalog.create_table("raw.users", schema=schema)

        # Create mock Trino
        trino = MockTrinoResource()

        # Append data to Iceberg
        df = pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})
        table.append(df)

        # Verify data is there
        result = table.scan().to_pandas()
        assert len(result) == 3

        # Load to Trino
        trino.load_table("test.users", result)

        # Query from Trino
        cursor = trino.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM test_users")
        count_result = cursor.fetchall()

        assert count_result[0][0] == 3

    def test_local_mode_with_assets(self):
        """Test local mode with asset execution."""

        def test_asset(partition_date: str):
            """Return partitioned DataFrame rows."""
            return pd.DataFrame(
                {
                    "id": [1, 2],
                    "partition": [partition_date, partition_date],
                }
            )

        with local_test_mode():
            result = run_asset_test(
                test_asset,
                partition="2024-01-01",
            )

            assert result.success
            assert result.data is not None
            assert len(result.data) == 2
