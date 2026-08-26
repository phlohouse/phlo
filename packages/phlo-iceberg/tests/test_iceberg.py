"""Unit tests for phlo-iceberg catalog, table, and resource surfaces.

Survivor suite for the legacy mock-catalog tests: every test here either has no
equivalent elsewhere or is the strongest oracle after deduplication. Live
Nessie/MinIO coverage lives in test_integration_iceberg.py; maintenance,
inventory, evidence, and rollback contracts live in their dedicated contract
modules.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType, TimestampType

from phlo_iceberg.catalog import create_namespace, get_catalog, list_tables, reset_catalog_cache
from phlo_iceberg.cli_utils import get_iceberg_catalog
from phlo_iceberg.resource import IcebergResource
from phlo_iceberg.tables import (
    _align_arrow_table_to_target_schema,
    append_to_table,
    delete_table,
    ensure_table,
    get_table_schema,
    get_table_stats,
)


class TestCatalogOperations:
    """Unit tests for catalog operations."""

    @patch("phlo_iceberg.catalog.load_catalog")
    @patch("phlo_iceberg.catalog.get_settings")
    def test_get_catalog_creates_and_caches_catalog_instances_for_different_refs(
        self, mock_get_settings, mock_load_catalog
    ):
        """Test that get_catalog creates and caches catalog instances for different refs."""
        # Setup mocks
        mock_catalog_main = MagicMock()
        mock_catalog_dev = MagicMock()
        mock_load_catalog.side_effect = [mock_catalog_main, mock_catalog_dev]

        mock_settings = MagicMock()
        mock_settings.get_pyiceberg_catalog_config.side_effect = [
            {"type": "rest", "uri": "http://nessie:19120/iceberg/main"},
            {"type": "rest", "uri": "http://nessie:19120/iceberg/dev"},
        ]
        mock_get_settings.return_value = mock_settings

        # Clear cache
        get_catalog.cache_clear()

        # First call for main
        catalog1 = get_catalog("main")
        assert catalog1 == mock_catalog_main
        mock_load_catalog.assert_called_once_with(
            name="iceberg_main", type="rest", uri="http://nessie:19120/iceberg/main"
        )

        # Second call for main should return cached
        mock_load_catalog.reset_mock()
        catalog2 = get_catalog("main")
        assert catalog2 == mock_catalog_main
        mock_load_catalog.assert_not_called()  # Should use cache

        # Call for dev should create new
        catalog3 = get_catalog("dev")
        assert catalog3 == mock_catalog_dev
        mock_load_catalog.assert_called_once_with(
            name="iceberg_dev", type="rest", uri="http://nessie:19120/iceberg/dev"
        )

    @patch("phlo_iceberg.catalog.get_catalog")
    def test_list_tables_returns_correct_tables_for_namespaces_and_all_namespaces(
        self, mock_get_catalog
    ):
        """Test that list_tables returns correct tables for namespaces and all namespaces."""
        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog

        # Mock namespaces (PyIceberg returns list of tuples)
        mock_catalog.list_namespaces.return_value = [("raw",), ("bronze",)]

        # Mock tables in namespaces
        mock_table1 = MagicMock()
        mock_table1.__str__ = MagicMock(return_value="raw.entries")
        mock_table2 = MagicMock()
        mock_table2.__str__ = MagicMock(return_value="raw.treatments")
        mock_table3 = MagicMock()
        mock_table3.__str__ = MagicMock(return_value="bronze.entries")

        def mock_list_tables(namespace):
            """Return mocked table objects for the provided namespace."""
            if namespace == "raw":
                return [mock_table1, mock_table2]
            elif namespace == "bronze":
                return [mock_table3]
            else:
                return []

        mock_catalog.list_tables.side_effect = mock_list_tables

        # Test listing specific namespace
        tables_raw = list_tables("raw")
        assert tables_raw == ["raw.entries", "raw.treatments"]
        mock_catalog.list_tables.assert_called_with("raw")

        # Test listing all namespaces
        mock_catalog.list_tables.reset_mock()
        all_tables = list_tables()
        assert all_tables == ["raw.entries", "raw.treatments", "bronze.entries"]
        assert mock_catalog.list_tables.call_count == 2  # Called for each namespace

    @patch("phlo_iceberg.catalog.get_catalog")
    def test_create_namespace_handles_existing_namespaces_without_errors(self, mock_get_catalog):
        """Test that create_namespace handles existing namespaces without errors."""
        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog

        # Test successful creation
        mock_catalog.create_namespace.return_value = None
        create_namespace("raw")
        mock_catalog.create_namespace.assert_called_with("raw")

        # Test existing namespace (should not raise error)
        mock_catalog.create_namespace.side_effect = Exception("Namespace already exists")
        create_namespace("raw")  # Should not raise
        assert mock_catalog.create_namespace.call_count == 2

    @patch("phlo_iceberg.catalog.logger")
    @patch("phlo_iceberg.catalog.get_catalog")
    def test_create_namespace_logs_existing_namespace_without_stack(
        self, mock_get_catalog, mock_logger
    ):
        """Existing namespaces are an idempotent condition, not a warning."""
        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog
        mock_catalog.create_namespace.side_effect = NamespaceAlreadyExistsError(
            "Namespace already exists: raw"
        )

        create_namespace("raw")

        mock_logger.info.assert_any_call(
            "iceberg_catalog_create_namespace_exists",
            namespace="raw",
            ref="main",
        )
        mock_logger.warning.assert_not_called()

    def test_reset_catalog_cache_clears_cli_utils_cache(self):
        """reset_catalog_cache should clear the CLI-level catalog cache too."""
        get_catalog.cache_clear()
        get_iceberg_catalog.cache_clear()

        with patch(
            "phlo_iceberg.catalog.get_catalog", return_value=MagicMock()
        ) as mock_get_catalog:
            get_iceberg_catalog("main")
            get_iceberg_catalog("main")

            assert mock_get_catalog.call_count == 1

            reset_catalog_cache()
            get_iceberg_catalog("main")

            assert mock_get_catalog.call_count == 2

    def test_get_catalog_respects_explicit_s3_endpoint_override(self, monkeypatch):
        """Test environments can redirect S3 traffic away from default MinIO DNS aliases."""
        from phlo_iceberg.catalog import reset_catalog_cache
        from phlo_iceberg.settings import get_settings as get_iceberg_settings
        from phlo_minio.settings import get_settings as get_minio_settings
        from phlo_nessie.settings import get_settings as get_nessie_settings

        reset_catalog_cache()
        get_iceberg_settings.cache_clear()
        get_minio_settings.cache_clear()
        get_nessie_settings.cache_clear()
        monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://127.0.0.1:19001")

        try:
            with patch(
                "phlo_iceberg.catalog.load_catalog", return_value=MagicMock()
            ) as mock_load_catalog:
                get_catalog(ref="main")

            assert mock_load_catalog.call_args.kwargs["s3.endpoint"] == "http://127.0.0.1:19001"
        finally:
            reset_catalog_cache()
            get_iceberg_settings.cache_clear()
            get_minio_settings.cache_clear()
            get_nessie_settings.cache_clear()


class TestTableOperations:
    """Unit tests for table operations."""

    @pytest.mark.parametrize(
        ("partition_field", "field_type"),
        [
            (("region", "identity"), LongType()),
            (("event_time", "day"), TimestampType()),
        ],
    )
    def test_ensure_table_creates_new_table_with_schema_and_partition_spec(
        self, partition_field, field_type
    ):
        """New tables are created with the requested schema and partition spec."""
        field_name, transform = partition_field

        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = Exception("Table not found")
        mock_table = MagicMock()
        mock_catalog.create_table.return_value = mock_table

        schema = Schema(
            NestedField(1, "id", StringType(), required=True),
            NestedField(2, field_name, field_type, required=True),
        )

        with (
            patch("phlo_iceberg.tables.create_namespace") as mock_create_namespace,
            patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog),
        ):
            table = ensure_table("raw.entries", schema, [(field_name, transform)])

        mock_create_namespace.assert_called_once_with("raw", ref="main")
        call_args = mock_catalog.create_table.call_args
        assert call_args.kwargs["identifier"] == "raw.entries"
        assert call_args.kwargs["schema"] == schema
        assert "partition_spec" in call_args.kwargs
        assert table is mock_table

    def test_ensure_table_loads_existing_table(self):
        """ensure_table loads existing tables without creating new ones."""
        mock_catalog = MagicMock()
        mock_existing_table = MagicMock()
        mock_catalog.load_table.return_value = mock_existing_table

        schema = Schema(NestedField(1, "id", StringType(), required=True))

        with (
            patch("phlo_iceberg.tables.create_namespace"),
            patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog),
        ):
            table = ensure_table("raw.entries", schema)

        mock_catalog.load_table.assert_called_once_with("raw.entries")
        mock_catalog.create_table.assert_not_called()
        assert table is mock_existing_table

    def test_ensure_table_invalid_table_name(self):
        """ensure_table rejects table names without exactly one namespace dot."""
        schema = Schema(NestedField(1, "id", StringType(), required=True))

        with patch("phlo_iceberg.tables.get_catalog") as mock_get_catalog:
            with pytest.raises(ValueError, match="Table name must be namespace.table"):
                ensure_table("invalid_table_name", schema)

        mock_get_catalog.assert_called_once()

    def test_ensure_table_reloads_existing_after_create_race(self):
        """A lost create race falls back to loading the winning writer's table."""
        mock_catalog = MagicMock()
        mock_existing = MagicMock()
        mock_catalog.load_table.side_effect = [Exception("Table not found"), mock_existing]
        mock_catalog.create_table.side_effect = TableAlreadyExistsError("exists")

        schema = Schema(NestedField(1, "id", LongType(), required=True))

        with (
            patch("phlo_iceberg.tables.create_namespace"),
            patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog),
        ):
            result = ensure_table("ns.raced_table", schema)

        assert result is mock_existing
        assert mock_catalog.load_table.call_count == 2

    def test_ensure_table_rejects_unknown_partition_transform(self):
        """Unknown partition transforms fail instead of producing empty specs."""
        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = Exception("Not found")

        schema = Schema(NestedField(1, "id", StringType(), required=True))

        with (
            patch("phlo_iceberg.tables.create_namespace"),
            patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog),
        ):
            with pytest.raises(ValueError, match="Unknown transform"):
                ensure_table("ns.table", schema, [("id", "unknown_transform")])

    def test_ensure_table_forwards_ref_to_catalog_and_namespace(self):
        """Ref selection reaches both the catalog lookup and namespace creation."""
        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = Exception("Table not found")
        mock_catalog.create_table.return_value = MagicMock()

        schema = Schema(NestedField(1, "id", StringType(), required=True))

        with (
            patch("phlo_iceberg.tables.create_namespace") as mock_create_namespace,
            patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog) as mock_get_catalog,
        ):
            table = ensure_table("raw.entries", schema, ref="dev")

        mock_get_catalog.assert_called_with(ref="dev")
        mock_create_namespace.assert_called_with("raw", ref="dev")
        assert table is not None

    @patch("phlo_iceberg.tables.get_catalog")
    @patch("pyarrow.parquet.ParquetDataset")
    def test_append_to_table_handles_directories(self, mock_parquet_dataset, mock_get_catalog):
        """append_to_table treats directory paths as Parquet datasets."""
        import pyarrow as pa

        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog

        # Create a real schema for the mock table
        iceberg_schema = Schema(
            NestedField(1, "id", StringType(), required=True),
            NestedField(2, "name", StringType(), required=False),
        )

        mock_table = MagicMock()
        mock_table.schema.return_value = iceberg_schema
        mock_catalog.load_table.return_value = mock_table

        # Create a real arrow table with matching schema
        arrow_schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("name", pa.string()),
            ]
        )
        mock_arrow_table = pa.table({"id": ["1", "2"], "name": ["a", "b"]}, schema=arrow_schema)

        mock_dataset = MagicMock()
        mock_dataset.read.return_value = mock_arrow_table
        mock_parquet_dataset.return_value = mock_dataset

        # Mock Path.is_dir() to return True
        with patch("pathlib.Path.is_dir", return_value=True):
            result = append_to_table("raw.entries", "/path/to/data_dir")

        mock_parquet_dataset.assert_called_once_with("/path/to/data_dir")
        mock_dataset.read.assert_called_once()
        mock_table.append.assert_called_once()
        assert result["rows_inserted"] == 2

    def test_append_to_table_appends_parquet_file_rows(self, tmp_path):
        """append_to_table reads a parquet file and appends every row."""
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_table.schema.return_value = Schema(
            NestedField(1, "id", LongType(), required=True),
            NestedField(2, "name", StringType(), required=True),
        )
        mock_catalog.load_table.return_value = mock_table

        parquet_path = tmp_path / "data.parquet"
        pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]}).to_parquet(parquet_path)

        with patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog):
            result = append_to_table("ns.table", parquet_path)

        assert result["rows_inserted"] == 3
        assert result["rows_deleted"] == 0
        mock_table.append.assert_called_once()

    def test_append_to_table_drops_columns_outside_the_target_schema(self, tmp_path):
        """Parquet columns missing from the Iceberg schema are dropped, not rejected."""
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_table.schema.return_value = Schema(
            NestedField(1, "id", LongType(), required=True),
        )
        mock_catalog.load_table.return_value = mock_table

        parquet_path = tmp_path / "extra.parquet"
        pd.DataFrame({"id": [1, 2], "extra_col": ["x", "y"]}).to_parquet(parquet_path)

        with patch("phlo_iceberg.tables.get_catalog", return_value=mock_catalog):
            result = append_to_table("ns.table", parquet_path)

        assert result["rows_inserted"] == 2

    def test_align_arrow_table_to_target_schema_backfills_missing_nullable_columns(self):
        """Missing nullable target columns should be added as nulls before projection."""
        import pyarrow as pa

        arrow_table = pa.table({"id": ["1"], "name": ["bulbasaur"]})
        target_schema = pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("name", pa.string(), nullable=True),
                pa.field("habitat", pa.string(), nullable=True),
            ]
        )

        aligned = _align_arrow_table_to_target_schema(
            arrow_table, target_schema, table_name="raw.pokemon"
        )

        assert aligned.schema.names == ["id", "name", "habitat"]
        assert aligned.column("habitat").to_pylist() == [None]

    def test_align_arrow_table_to_target_schema_rejects_missing_required_columns(self):
        """Missing required target columns should fail explicitly."""
        import pyarrow as pa

        arrow_table = pa.table({"id": ["1"]})
        target_schema = pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("name", pa.string(), nullable=False),
            ]
        )

        with pytest.raises(ValueError, match="Required target column 'name'"):
            _align_arrow_table_to_target_schema(
                arrow_table, target_schema, table_name="raw.pokemon"
            )

    @patch("phlo_iceberg.tables.get_catalog")
    def test_get_table_schema_retrieves_schemas_from_existing_tables(self, mock_get_catalog):
        """Test that get_table_schema retrieves schemas from existing tables."""
        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog

        mock_table = MagicMock()
        mock_schema = MagicMock()
        mock_table.schema.return_value = mock_schema
        mock_catalog.load_table.return_value = mock_table

        schema = get_table_schema("raw.entries")

        mock_catalog.load_table.assert_called_once_with("raw.entries")
        mock_table.schema.assert_called_once()
        assert schema == mock_schema

    @patch("phlo_iceberg.tables.get_catalog")
    def test_delete_table_removes_tables_correctly(self, mock_get_catalog):
        """Test that delete_table removes tables correctly."""
        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog

        delete_table("raw.entries")

        mock_catalog.drop_table.assert_called_once_with("raw.entries")

    @patch("phlo_iceberg.tables.get_catalog")
    def test_get_table_stats_reports_empty_snapshot_baseline(self, mock_get_catalog):
        """Tables without snapshots report zeroed counts and no current snapshot."""
        mock_catalog = MagicMock()
        mock_get_catalog.return_value = mock_catalog
        mock_table = MagicMock()
        mock_table.snapshots.return_value = []
        mock_table.current_snapshot.return_value = None
        mock_table.location.return_value = "s3://lake/warehouse/ns/table"
        mock_catalog.load_table.return_value = mock_table

        stats = get_table_stats("ns.table")

        assert stats["table_name"] == "ns.table"
        assert stats["snapshot_count"] == 0
        assert stats["current_snapshot_id"] is None
        assert stats["file_count"] == 0
        assert stats["total_records"] == 0


class TestIcebergResourceSurface:
    """Unit tests for the IcebergResource facade over catalog and tables."""

    def test_get_catalog_respects_configured_and_override_refs(self):
        """get_catalog uses the configured ref unless an override is supplied."""
        mock_catalog = MagicMock()

        with patch(
            "phlo_iceberg.resource.get_catalog", return_value=mock_catalog
        ) as mock_resource_get_catalog:
            resource = IcebergResource(ref="feature-branch")

            returned = resource.get_catalog()
            mock_resource_get_catalog.assert_called_once_with(ref="feature-branch")
            assert returned is mock_catalog

            mock_resource_get_catalog.reset_mock()
            resource.get_catalog(override_ref="hotfix")
            mock_resource_get_catalog.assert_called_once_with(ref="hotfix")

    @patch("phlo_iceberg.resource.table_state")
    @patch("phlo_iceberg.resource.get_catalog")
    def test_observe_table_state_maps_provider_state_to_neutral_observation(
        self, mock_get_catalog, mock_table_state
    ):
        """observe_table_state exposes provider state through neutral field names."""
        mock_table_state.return_value = {
            "state": "present",
            "snapshot_id": "snapshot-1",
            "schema_hash": "schema-1",
            "metadata": {"snapshot": "observed"},
        }

        observed = IcebergResource(ref="dev").observe_table_state(
            table_name="raw.entries", override_ref="feature"
        )

        mock_get_catalog.assert_called_once_with(ref="feature")
        assert observed.state == "present"
        assert observed.revision == "snapshot-1"
        assert observed.schema_hash == "schema-1"

    @patch("phlo_iceberg.resource.ensure_table")
    def test_ensure_table_delegates_with_partition_list_and_ref(self, mock_ensure_table):
        """ensure_table forwards tuple specs as lists plus the configured ref."""
        mock_table = MagicMock()
        mock_ensure_table.return_value = mock_table

        schema = MagicMock()
        partition_spec = [("timestamp", "day")]

        result = IcebergResource(ref="dev").ensure_table(
            table_name="raw.entries", schema=schema, partition_spec=partition_spec
        )

        mock_ensure_table.assert_called_once_with(
            table_name="raw.entries", schema=schema, partition_spec=list(partition_spec), ref="dev"
        )
        assert result is mock_table

    @patch("phlo_iceberg.resource.append_to_table")
    def test_append_parquet_delegates_with_ref(self, mock_append_to_table):
        """append_parquet forwards the data path plus the configured ref."""
        IcebergResource(ref="dev").append_parquet(
            table_name="raw.entries", data_path="/path/to/data.parquet"
        )

        mock_append_to_table.assert_called_once_with(
            table_name="raw.entries", data_path="/path/to/data.parquet", ref="dev"
        )

    def test_support_advertises_refs_partition_transforms_and_maintenance_surface(self):
        """Support metadata pins the branch, transform, and maintenance surface."""
        support = IcebergResource().support

        assert support.supports_refs is True
        assert support.supports_snapshots is True
        assert support.partition_transforms == frozenset(
            {"identity", "day", "hour", "month", "year"}
        )
        assert support.supports_vacuum is False
        assert support.supports_compaction is True


def test_public_module_exports_are_available():
    """The package surface keeps its documented maintenance and table helpers."""
    import phlo_iceberg

    expected = [
        "append_to_table",
        "ensure_table",
        "expire_snapshots",
        "get_catalog",
        "get_table_stats",
        "merge_to_table",
        "remove_orphan_files",
    ]

    for name in expected:
        assert hasattr(phlo_iceberg, name), f"Missing export: {name}"
