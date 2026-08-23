"""Comprehensive integration tests for phlo-trino.

Per TEST_STRATEGY.md Level 2 (Functional):
- Query Execution: Execute simple SQL against a Trino container
- Type Mapping: Verify Trino-to-Pandas type conversions
- Catalog Generation: Verify catalog file generation
- Resource Management: Test connection lifecycle and error handling
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = pytest.mark.integration


# =============================================================================
# Unit Tests: Type Mapping (No external dependencies)
# =============================================================================


class TestTrinoTypeMappingUnit:
    """Unit tests for Trino type mapping utilities."""

    def test_trino_type_to_pandas_integers(self):
        """Test integer type conversions."""
        from phlo_trino.type_mapping import trino_type_to_pandas

        assert trino_type_to_pandas("tinyint") == "int8"
        assert trino_type_to_pandas("smallint") == "int16"
        assert trino_type_to_pandas("integer") == "int32"
        assert trino_type_to_pandas("bigint") == "int64"
        assert trino_type_to_pandas("int") == "int64"

    def test_trino_type_to_pandas_floats(self):
        """Test float type conversions."""
        from phlo_trino.type_mapping import trino_type_to_pandas

        assert trino_type_to_pandas("real") == "float32"
        assert trino_type_to_pandas("double") == "float64"
        assert trino_type_to_pandas("float") == "float64"
        assert trino_type_to_pandas("decimal") == "float64"

    def test_trino_type_to_pandas_strings(self):
        """Test string type conversions."""
        from phlo_trino.type_mapping import trino_type_to_pandas

        assert trino_type_to_pandas("varchar") == "string"
        assert trino_type_to_pandas("varchar(255)") == "string"
        assert trino_type_to_pandas("char") == "string"
        assert trino_type_to_pandas("json") == "string"
        assert trino_type_to_pandas("uuid") == "string"

    def test_trino_type_to_pandas_timestamps(self):
        """Test timestamp type conversions."""
        from phlo_trino.type_mapping import trino_type_to_pandas

        assert trino_type_to_pandas("timestamp") == "datetime64[ns]"
        assert trino_type_to_pandas("timestamp with time zone") == "datetime64[ns, UTC]"
        assert trino_type_to_pandas("date") == "datetime64[ns]"

    def test_trino_type_to_pandas_other(self):
        """Test other type conversions."""
        from phlo_trino.type_mapping import trino_type_to_pandas

        assert trino_type_to_pandas("boolean") == "bool"
        assert trino_type_to_pandas("varbinary") == "bytes"
        assert trino_type_to_pandas("unknown_type") == "object"

    def test_trino_type_normalization(self):
        """Test type string normalization (case insensitive, strips params)."""
        from phlo_trino.type_mapping import trino_type_to_pandas

        assert trino_type_to_pandas("BIGINT") == "int64"
        assert trino_type_to_pandas("  VARCHAR(100)  ") == "string"
        assert trino_type_to_pandas("Decimal(10,2)") == "float64"


class TestApplySchemaTypes:
    """Test schema-aware type application."""

    def test_apply_schema_types_basic(self):
        """Test applying schema types to DataFrame."""
        from phlo_trino.type_mapping import apply_schema_types
        from pandera.pandas import DataFrameModel

        class TestSchema(DataFrameModel):
            """Schema for verifying primitive type coercion."""

            id: int
            name: str
            value: float

        df = pd.DataFrame(
            {"id": ["1", "2", "3"], "name": [1, 2, 3], "value": ["1.5", "2.5", "3.5"]}
        )

        result = apply_schema_types(df, TestSchema)

        # Check types were converted
        assert result["id"].dtype.name in ("Int64", "int64")
        assert result["name"].dtype == "string"
        assert result["value"].dtype == "float64"

    def test_apply_schema_types_missing_columns(self):
        """Test handling of columns not in DataFrame."""
        from phlo_trino.type_mapping import apply_schema_types
        from pandera.pandas import DataFrameModel

        class TestSchema(DataFrameModel):
            """Schema including a column absent from input DataFrame."""

            id: int
            name: str
            missing_col: float  # Not in DataFrame

        df = pd.DataFrame({"id": ["1", "2"], "name": [1, 2]})

        # Should not raise, should skip missing columns
        result = apply_schema_types(df, TestSchema)
        assert "missing_col" not in result.columns


# =============================================================================
# Unit Tests: TrinoResource (Mocked connections)
# =============================================================================


class TestTrinoResourceUnit:
    """Unit tests for TrinoResource without real connections."""

    def test_trino_resource_initialization(self):
        """Test TrinoResource can be instantiated with defaults."""
        from phlo_trino import TrinoResource

        resource = TrinoResource()
        assert resource.user == "dagster"
        assert resource.host is None  # Uses config default
        assert resource.port is None  # Uses config default

    def test_trino_resource_custom_params(self):
        """Test TrinoResource with custom parameters."""
        from phlo_trino import TrinoResource

        resource = TrinoResource(
            host="custom-host", port=9999, user="test_user", catalog="test_catalog"
        )

        assert resource.host == "custom-host"
        assert resource.port == 9999
        assert resource.user == "test_user"
        assert resource.catalog == "test_catalog"

    def test_resolved_catalog_without_ref(self):
        """Test catalog resolution without branch reference."""
        from phlo_trino import TrinoResource

        with patch("phlo_trino.resource.config") as mock_config:
            mock_config.trino_catalog = "iceberg"
            mock_config.default_catalog_ref = "main"

            resource = TrinoResource(catalog="iceberg")
            assert resource._resolved_catalog() == "iceberg"

    def test_resolved_catalog_with_ref(self):
        """Test catalog resolution with branch reference."""
        from phlo_trino import TrinoResource

        with patch("phlo_trino.resource.config") as mock_config:
            mock_config.trino_catalog = "iceberg"
            mock_config.default_catalog_ref = "dev"

            resource = TrinoResource(catalog="iceberg", ref="dev")
            assert resource._resolved_catalog() == "iceberg_dev"

    def test_resolved_catalog_prefers_runtime_ref(self):
        """Runtime routing should override the configured default ref."""
        from phlo_trino import TrinoResource

        with patch("phlo_trino.resource.config") as mock_config:
            mock_config.trino_catalog = "iceberg"
            mock_config.default_catalog_ref = "main"

            runtime = type(
                "StubRuntime",
                (),
                {
                    "run_id": "run-1",
                    "partition_key": None,
                    "tags": {"phlo/ref": "feature_orders"},
                    "resources": {},
                    "logger": property(lambda self: object()),
                    "routing": property(lambda self: (_ for _ in ()).throw(AttributeError())),
                    "get_resource": lambda self, name: None,
                },
            )()

            resource = TrinoResource(catalog="iceberg", runtime=runtime)
            assert resource._resolved_catalog() == "iceberg_feature_orders"

    def test_connection_provisions_exact_runtime_ref_before_querying(self):
        """The WAP catalog must be created against the same branch as the query."""
        from phlo_trino import TrinoResource

        bootstrap_cursor = MagicMock()
        bootstrap_cursor.__enter__.return_value = bootstrap_cursor
        bootstrap = MagicMock()
        bootstrap.cursor.return_value = bootstrap_cursor
        query_connection = MagicMock()

        with (
            patch("phlo_trino.resource.config") as mock_config,
            patch(
                "phlo_trino.resource.connect", side_effect=[bootstrap, query_connection]
            ) as connect,
        ):
            mock_config.trino_catalog = "iceberg"
            mock_config.default_catalog_ref = "main"
            mock_config.trino_host = "trino"
            mock_config.trino_port = 8080
            resource = TrinoResource(ref="pipeline-run-1")

            assert resource.get_connection(schema="raw") is query_connection

        statement = bootstrap_cursor.execute.call_args.args[0]
        assert 'CREATE CATALOG IF NOT EXISTS "iceberg_pipeline-run-1"' in statement
        assert "\"iceberg.nessie-catalog.ref\" = 'pipeline-run-1'" in statement
        assert connect.call_args_list[0].kwargs["catalog"] == "system"
        assert connect.call_args_list[1].kwargs["catalog"] == "iceberg_pipeline-run-1"
        bootstrap.close.assert_called_once()

    def test_trino_implements_neutral_ref_query_catalog_manager(self):
        """The core contract is satisfied without exposing Trino to orchestrators."""
        from phlo.capabilities import RefQueryCatalogManager
        from phlo_trino import TrinoResource

        assert isinstance(TrinoResource(), RefQueryCatalogManager)

    def test_public_catalog_provision_is_deterministic_and_owned(self):
        """Provisioning derives the same catalog identity from an owned run ref."""
        from phlo_trino import TrinoResource

        resource = TrinoResource(catalog="iceberg")
        with (
            patch("phlo_trino.resource.config") as mock_config,
            patch.object(resource, "_provision_catalog") as provision,
        ):
            mock_config.trino_catalog = "iceberg"

            assert (
                resource.provision_ref_query_catalog("pipeline-run-1") == "iceberg_pipeline-run-1"
            )

        provision.assert_called_once_with("iceberg_pipeline-run-1", "pipeline-run-1")

        with pytest.raises(ValueError, match="owned pipeline-run"):
            resource.provision_ref_query_catalog("dev")

    def test_connection_keeps_non_wap_refs_on_their_existing_catalogs(self):
        """A normal ref cannot claim ownership of a dynamic WAP catalog."""
        from phlo_trino import TrinoResource

        query_connection = MagicMock()
        with (
            patch("phlo_trino.resource.config") as mock_config,
            patch("phlo_trino.resource.connect", return_value=query_connection) as connect,
        ):
            mock_config.trino_catalog = "iceberg"
            mock_config.default_catalog_ref = "main"
            mock_config.trino_host = "trino"
            mock_config.trino_port = 8080
            resource = TrinoResource(ref="dev")

            assert resource.get_connection(schema="raw") is query_connection

        connect.assert_called_once()
        assert connect.call_args.kwargs["catalog"] == "iceberg_dev"

    def test_drop_ref_query_catalog_only_allows_owned_non_main_catalog(self):
        """Explicit cleanup cannot remove the shared main catalog."""
        from phlo_trino import TrinoResource

        bootstrap_cursor = MagicMock()
        bootstrap_cursor.__enter__.return_value = bootstrap_cursor
        bootstrap = MagicMock()
        bootstrap.cursor.return_value = bootstrap_cursor

        with (
            patch("phlo_trino.resource.config") as mock_config,
            patch("phlo_trino.resource.connect", return_value=bootstrap),
        ):
            mock_config.trino_catalog = "iceberg"
            mock_config.trino_host = "trino"
            mock_config.trino_port = 8080
            resource = TrinoResource()
            resource.drop_ref_query_catalog("pipeline-run-1")
            with pytest.raises(ValueError, match="owned pipeline-run"):
                resource.drop_ref_query_catalog("main")

        assert (
            bootstrap_cursor.execute.call_args.args[0]
            == 'DROP CATALOG IF EXISTS "iceberg_pipeline-run-1"'
        )

    def test_execute_with_mocked_connection(self):
        """Test execute method with mocked Trino connection."""
        from phlo_trino import TrinoResource

        mock_cursor = MagicMock()
        mock_cursor.description = [("col1",), ("col2",)]
        mock_cursor.fetchall.return_value = [(1, "a"), (2, "b")]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        resource = TrinoResource(host="test", port=8080)

        with patch.object(resource, "get_connection", return_value=mock_conn):
            result = resource.execute("SELECT 1")

            assert result == [(1, "a"), (2, "b")]
            mock_cursor.execute.assert_called_once_with("SELECT 1", [])
            mock_cursor.close.assert_called_once()
            mock_conn.close.assert_called_once()

    def test_execute_with_no_results(self):
        """Test execute when query returns no results (e.g., DDL)."""
        from phlo_trino import TrinoResource

        mock_cursor = MagicMock()
        mock_cursor.description = None  # No result set

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        resource = TrinoResource(host="test", port=8080)

        with patch.object(resource, "get_connection", return_value=mock_conn):
            result = resource.execute("CREATE TABLE test (id INT)")

            assert result == []

    def test_wait_ready_retries_until_success(self):
        """Wait ready should retry on startup errors and return once healthy."""
        from phlo_trino import TrinoResource

        resource = TrinoResource(host="test", port=8080)
        calls = {"count": 0}

        def side_effect(*_args, **_kwargs):
            """Fail twice with startup error before returning success."""
            calls["count"] += 1
            if calls["count"] < 3:
                raise Exception("SERVER_STARTING_UP")
            return [(1,)]

        with patch.object(resource, "execute", side_effect=side_effect):
            resource.wait_ready(timeout=1.0, interval=0.0, schema="raw")

        assert calls["count"] == 3

    def test_wait_ready_times_out(self):
        """Wait ready should raise after timeout if Trino never starts."""
        from phlo_trino import TrinoResource

        resource = TrinoResource(host="test", port=8080)

        with patch.object(resource, "execute", side_effect=Exception("SERVER_STARTING_UP")):
            with pytest.raises(TimeoutError):
                resource.wait_ready(timeout=0.01, interval=0.0, schema="raw")


# =============================================================================
# Unit Tests: Catalog Generator
# =============================================================================


class TestCatalogGenerator:
    """Tests for Trino catalog file generation."""

    def test_generate_catalog_files_creates_directory(self):
        """Test that output directory is created if it doesn't exist."""
        from phlo_trino.catalog_generator import generate_catalog_files

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "new_catalog_dir"

            with patch("phlo_trino.catalog_generator.discover_trino_catalogs", return_value=[]):
                result = generate_catalog_files(output_dir)

            assert output_dir.exists()
            assert result == {}

    def test_generate_catalog_files_with_catalog_plugin(self):
        """Test catalog file generation with a mock catalog plugin."""
        from phlo_trino.catalog_generator import generate_catalog_files
        from phlo.plugins.base import CatalogPlugin, PluginMetadata

        class MockCatalog(CatalogPlugin):
            """Mock catalog plugin used for catalog file generation tests."""

            @property
            def metadata(self):
                """Return mock plugin metadata."""
                return PluginMetadata("mock", "1.0.0", "Mock catalog")

            @property
            def targets(self) -> list[str]:
                """Advertise the single supported Trino target."""
                return ["trino"]

            @property
            def catalog_name(self) -> str:
                """Return the mock catalog identifier."""
                return "mock_catalog"

            def get_properties(self):
                """Return the connector property map written into the generated file."""
                return {"connector.name": "mock", "key": "value"}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            with patch(
                "phlo_trino.catalog_generator.discover_trino_catalogs", return_value=[MockCatalog()]
            ):
                result = generate_catalog_files(output_dir)

            assert "mock_catalog" in result
            assert result["mock_catalog"].exists()

            content = result["mock_catalog"].read_text()
            assert "connector.name=mock" in content
            assert "key=value" in content


# =============================================================================
# Service Plugin Tests
# =============================================================================


class TestTrinoServicePlugin:
    """Test Trino service plugin registration."""

    def test_plugin_initializes(self):
        """Test that TrinoServicePlugin can be instantiated."""
        from phlo_trino import TrinoServicePlugin

        plugin = TrinoServicePlugin()
        assert plugin is not None

    def test_plugin_metadata(self):
        """Test plugin metadata is correctly defined."""
        from phlo_trino import TrinoServicePlugin

        plugin = TrinoServicePlugin()
        metadata = plugin.metadata

        assert metadata.name == "trino"
        assert metadata.version is not None
        assert "query" in metadata.tags or "sql" in metadata.tags

    def test_service_definition_loads(self):
        """Test service definition YAML can be loaded."""
        from phlo_trino import TrinoServicePlugin

        plugin = TrinoServicePlugin()
        service_def = plugin.service_definition

        assert isinstance(service_def, dict)
        # Service definitions have flat structure with 'name' and 'compose' keys
        assert "name" in service_def or "compose" in service_def

    def test_resource_provider_registers_query_engine_capability(self):
        """Trino resource provider should expose query-engine capability metadata."""
        from phlo.capabilities import CapabilitySupport
        from phlo_trino.plugin import TrinoResourceProvider
        from phlo_trino.resource import TrinoResource

        provider = TrinoResourceProvider()
        engines = provider.get_query_engines()

        assert len(engines) == 1
        assert engines[0].name == "trino"
        assert isinstance(engines[0].provider, TrinoResource)
        assert engines[0].support == CapabilitySupport(
            supports_refs=True,
            supports_time_travel=True,
        )
        assert engines[0].metadata["compatibility"] == {
            "target": "apache-iceberg-1.11",
            "rest_catalog": {"trino_ref_strategy": "rest-catalog-prefix"},
            "engines": {
                "trino": {
                    "catalog_type": "rest",
                    "iceberg_table_spec_versions": [1, 2],
                }
            },
            "checks": ["trino-prefix-property", "trino-table-spec-v1-v2"],
        }


# =============================================================================
# Functional Integration Tests (Real Trino if available)
# =============================================================================


@pytest.fixture
def trino_service():
    """Fixture that provides a Trino connection if available."""
    from phlo_trino import TrinoResource

    # Check if Trino is available
    host = os.environ.get("TRINO_HOST", "localhost")
    port = int(os.environ.get("TRINO_PORT", "8080"))

    resource = TrinoResource(host=host, port=port)

    try:
        # Try to connect
        result = resource.execute("SELECT 1")
        if result and list(result[0]) == [1]:
            yield resource
        else:
            pytest.skip("Trino returned unexpected result")
    except Exception as e:
        pytest.skip(f"Trino not available: {e}")


class TestTrinoIntegrationReal:
    """Real integration tests against a running Trino instance."""

    def test_simple_query(self, trino_service):
        """Test simple query execution against real Trino."""
        result = trino_service.execute("SELECT 1 AS one, 'hello' AS greeting")

        assert len(result) == 1
        assert result[0][0] == 1
        assert result[0][1] == "hello"

    def test_system_catalog_query(self, trino_service):
        """Test querying system catalog."""
        result = trino_service.execute("SELECT catalog_name FROM system.metadata.catalogs LIMIT 5")

        assert len(result) >= 1
        # Should have at least system catalog
        catalog_names = [row[0] for row in result]
        assert "system" in catalog_names

    def test_multiple_rows_query(self, trino_service):
        """Test query returning multiple rows."""
        result = trino_service.execute("SELECT x FROM (VALUES 1, 2, 3, 4, 5) AS t(x)")

        assert len(result) == 5
        values = [row[0] for row in result]
        assert values == [1, 2, 3, 4, 5]

    def test_parameterized_query(self, trino_service):
        """Test query with parameters."""
        # Note: Trino uses ? for parameters
        result = trino_service.execute("SELECT ? + ? AS sum", params=[10, 20])

        assert len(result) == 1
        assert result[0][0] == 30

    def test_context_manager_cleanup(self, trino_service):
        """Test that cursor context manager properly cleans up."""
        from phlo_trino import TrinoResource

        host = os.environ.get("TRINO_HOST", "localhost")
        port = int(os.environ.get("TRINO_PORT", "8080"))
        resource = TrinoResource(host=host, port=port)

        with resource.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchall()

        assert [list(row) for row in result] == [[1]]
        # Cursor and connection should be closed after context


# =============================================================================
# Version and Export Tests
# =============================================================================


class TestTrinoExports:
    """Test module exports and version."""

    def test_version_defined(self):
        """Test that version is defined."""
        from importlib.metadata import version

        import phlo_trino

        assert hasattr(phlo_trino, "__version__")
        assert phlo_trino.__version__ == version("phlo-trino")

    def test_expected_exports(self):
        """Test that expected classes are exported."""
        from phlo_trino import TrinoResource, TrinoServicePlugin

        assert TrinoResource is not None
        assert TrinoServicePlugin is not None
