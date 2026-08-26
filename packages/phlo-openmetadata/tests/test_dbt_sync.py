"""Tests for the dbt manifest parser and OpenMetadata syncer.

Covers load failure semantics (missing manifest raises, missing catalog is
treated as empty), table extraction merging catalog column types into
manifest-documented columns, tag and freshness propagation, and sync
statistics including per-model filtering.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from phlo_openmetadata.dbt_sync import DbtManifestParser
from phlo_openmetadata.settings import OpenMetadataSettings


@pytest.fixture
def sample_manifest():
    """Sample dbt manifest for testing."""
    return {
        "nodes": {
            "model.my_project.stg_glucose_entries": {
                "name": "stg_glucose_entries",
                "schema": "bronze",
                "description": "Staging glucose entries",
                "columns": {
                    "id": {
                        "name": "id",
                        "description": "Unique identifier",
                    },
                    "value": {
                        "name": "value",
                        "description": "Glucose value in mg/dL",
                    },
                },
                "depends_on": {"nodes": ["source.my_project.nightscout.glucose_entries"]},
                "tags": ["glucose", "staging"],
                "freshness": {"warn_after": {"count": 24, "period": "hour"}},
            },
            "model.my_project.fct_glucose_readings": {
                "name": "fct_glucose_readings",
                "schema": "silver",
                "description": "Fact table for glucose readings",
                "columns": {
                    "reading_id": {"description": "Reading ID"},
                },
                "depends_on": {"nodes": ["model.my_project.stg_glucose_entries"]},
                "tags": ["glucose", "fact"],
            },
        },
        "sources": {
            "source.my_project.nightscout.glucose_entries": {
                "source_name": "nightscout",
                "name": "glucose_entries",
                "description": "Raw glucose data from Nightscout API",
            }
        },
    }


@pytest.fixture
def sample_catalog():
    """Sample dbt catalog for testing."""
    return {
        "nodes": {
            "model.my_project.stg_glucose_entries": {
                "metadata": {
                    "name": "stg_glucose_entries",
                    "schema": "bronze",
                },
                "columns": {
                    "id": {
                        "name": "id",
                        "type": "INTEGER",
                        "index": 1,
                        "description": "Unique identifier",
                    },
                    "value": {
                        "name": "value",
                        "type": "DOUBLE",
                        "index": 2,
                        "description": "Glucose value",
                    },
                    "timestamp": {
                        "name": "timestamp",
                        "type": "TIMESTAMP",
                        "index": 3,
                        "description": "Reading timestamp",
                    },
                },
            }
        }
    }


@pytest.fixture
def manifest_file(sample_manifest):
    """Create temporary manifest file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_manifest, f)
        path = f.name
    yield path
    Path(path).unlink()


@pytest.fixture
def catalog_file(sample_catalog):
    """Create temporary catalog file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_catalog, f)
        path = f.name
    yield path
    Path(path).unlink()


class TestDbtManifestParser:
    """Tests for DbtManifestParser."""

    def test_from_settings(self):
        """Parser paths come from OpenMetadata-owned settings."""
        settings = OpenMetadataSettings(
            openmetadata_dbt_manifest_path="/tmp/manifest.json",
            openmetadata_dbt_catalog_path="/tmp/catalog.json",
        )

        parser = DbtManifestParser.from_settings(settings)

        assert parser.manifest_path == Path("/tmp/manifest.json")
        assert parser.catalog_path == Path("/tmp/catalog.json")

    def test_parser_initialization(self, manifest_file, catalog_file):
        """Test parser initialization."""
        parser = DbtManifestParser(manifest_file, catalog_file)

        assert parser.manifest_path == Path(manifest_file)
        assert parser.catalog_path == Path(catalog_file)
        assert parser.manifest is None
        assert parser.catalog is None

    def test_load_manifest(self, manifest_file, sample_manifest):
        """Test loading manifest."""
        parser = DbtManifestParser(manifest_file)
        manifest = parser.load_manifest()

        assert manifest == sample_manifest
        assert parser.manifest == sample_manifest

    def test_load_manifest_not_found(self):
        """Test loading non-existent manifest."""
        parser = DbtManifestParser("/nonexistent/manifest.json")

        with pytest.raises(FileNotFoundError):
            parser.load_manifest()

    def test_load_manifest_invalid_json(self):
        """Test loading invalid JSON manifest."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json {")
            path = f.name

        try:
            parser = DbtManifestParser(path)
            with pytest.raises(json.JSONDecodeError):
                parser.load_manifest()
        finally:
            Path(path).unlink()

    def test_load_catalog(self, manifest_file, catalog_file, sample_catalog):
        """Test loading catalog."""
        parser = DbtManifestParser(manifest_file, catalog_file)
        catalog = parser.load_catalog()

        assert catalog == sample_catalog
        assert parser.catalog == sample_catalog

    def test_load_catalog_not_found(self, manifest_file):
        """Test loading non-existent catalog."""
        parser = DbtManifestParser(manifest_file, "/nonexistent/catalog.json")
        catalog = parser.load_catalog()

        assert catalog == {}

    def test_get_models(self, manifest_file, sample_manifest):
        """Test getting models from manifest."""
        parser = DbtManifestParser(manifest_file)
        models = parser.get_models(sample_manifest)

        assert len(models) == 2
        assert "stg_glucose_entries" in models["model.my_project.stg_glucose_entries"]["name"]
        assert "fct_glucose_readings" in models["model.my_project.fct_glucose_readings"]["name"]

    def test_get_model_columns(self, manifest_file, catalog_file, sample_catalog):
        """Test getting model columns."""
        parser = DbtManifestParser(manifest_file, catalog_file)
        columns = parser.get_model_columns("stg_glucose_entries", "bronze", sample_catalog)

        assert len(columns) == 3
        assert columns["id"]["type"] == "INTEGER"
        assert columns["value"]["type"] == "DOUBLE"
        assert columns["timestamp"]["type"] == "TIMESTAMP"

    def test_get_model_columns_not_in_catalog(self, manifest_file, catalog_file):
        """Test getting columns for model not in catalog."""
        parser = DbtManifestParser(manifest_file, catalog_file)
        columns = parser.get_model_columns("nonexistent_model", "bronze")

        assert columns == {}

    def test_get_model_tests(self, manifest_file, sample_manifest):
        """Test getting model tests."""
        # Add a test to manifest
        sample_manifest["nodes"]["test.my_project.not_null_id"] = {
            "name": "not_null_id",
            "test_metadata": {
                "name": "not_null",
                "kwargs": {"column_name": "id"},
            },
            "fqn": ["stg_glucose_entries", "not_null_id"],
            "depends_on": {"nodes": ["model.my_project.stg_glucose_entries"]},
        }

        parser = DbtManifestParser(manifest_file)
        tests = parser.get_model_tests("model.my_project.stg_glucose_entries", sample_manifest)

        assert len(tests) == 1

    def test_extract_openmetadata_table_from_manifest(self, manifest_file, sample_manifest):
        """Test extracting OpenMetadata table from manifest."""
        parser = DbtManifestParser(manifest_file)
        model = sample_manifest["nodes"]["model.my_project.stg_glucose_entries"]

        om_table = parser.extract_openmetadata_table(model, "bronze")

        assert om_table.name == "stg_glucose_entries"
        assert om_table.description == "Staging glucose entries"
        assert om_table.columns is not None
        assert len(om_table.columns) == 2
        assert om_table.columns[0].name == "id"

    def test_extract_openmetadata_table_with_columns_info(
        self, manifest_file, sample_manifest, sample_catalog
    ):
        """Test extracting table with column info from catalog."""
        parser = DbtManifestParser(manifest_file)
        model = sample_manifest["nodes"]["model.my_project.stg_glucose_entries"]
        columns_info = sample_catalog["nodes"]["model.my_project.stg_glucose_entries"]["columns"]

        om_table = parser.extract_openmetadata_table(model, "bronze", columns_info)

        # Model has 2 documented columns; catalog info supplements dataType
        assert om_table.columns is not None
        assert len(om_table.columns) == 2
        # Check that dataType comes from columns_info
        value_col = next(c for c in om_table.columns if c.name == "value")
        assert value_col.dataType == "DOUBLE"

    def test_extract_openmetadata_table_with_tags(self, manifest_file, sample_manifest):
        """Test that tags are included in extracted table."""
        parser = DbtManifestParser(manifest_file)
        model = sample_manifest["nodes"]["model.my_project.stg_glucose_entries"]

        om_table = parser.extract_openmetadata_table(model, "bronze")

        assert om_table.tags is not None
        tag_names = [t["name"] for t in om_table.tags]
        assert "glucose" in tag_names
        assert "staging" in tag_names

    def test_extract_openmetadata_table_with_freshness(self, manifest_file, sample_manifest):
        """Test that freshness is included as tag."""
        parser = DbtManifestParser(manifest_file)
        model = sample_manifest["nodes"]["model.my_project.stg_glucose_entries"]

        om_table = parser.extract_openmetadata_table(model, "bronze")

        freshness_tags = [t for t in om_table.tags or [] if "freshness" in t["name"]]
        assert len(freshness_tags) == 1

    @patch("phlo_openmetadata.dbt_sync.DbtManifestParser.load_manifest")
    @patch("phlo_openmetadata.dbt_sync.DbtManifestParser.load_catalog")
    def test_sync_to_openmetadata(
        self,
        mock_load_catalog,
        mock_load_manifest,
        manifest_file,
        sample_manifest,
        sample_catalog,
    ):
        """Test syncing models to OpenMetadata."""
        mock_load_manifest.return_value = sample_manifest
        mock_load_catalog.return_value = sample_catalog

        parser = DbtManifestParser(manifest_file)
        om_client = Mock()

        stats = parser.sync_to_openmetadata(om_client, schema_name="bronze")

        assert stats["created"] == 2
        assert om_client.create_or_update_table.called

    @patch("phlo_openmetadata.dbt_sync.DbtManifestParser.load_manifest")
    @patch("phlo_openmetadata.dbt_sync.DbtManifestParser.load_catalog")
    def test_sync_with_model_filter(
        self,
        mock_load_catalog,
        mock_load_manifest,
        manifest_file,
        sample_manifest,
        sample_catalog,
    ):
        """Test syncing with model filter."""
        mock_load_manifest.return_value = sample_manifest
        mock_load_catalog.return_value = sample_catalog

        parser = DbtManifestParser(manifest_file)
        om_client = Mock()

        parser.sync_to_openmetadata(
            om_client,
            schema_name="bronze",
            model_filter=["stg_glucose_entries"],
        )

        # Should only sync one model
        assert om_client.create_or_update_table.call_count == 1

    @patch("phlo_openmetadata.dbt_sync.DbtManifestParser.load_manifest")
    @patch("phlo_openmetadata.dbt_sync.DbtManifestParser.load_catalog")
    def test_sync_with_failure(
        self,
        mock_load_catalog,
        mock_load_manifest,
        manifest_file,
        sample_manifest,
        sample_catalog,
    ):
        """Test sync with partial failure."""
        mock_load_manifest.return_value = sample_manifest
        mock_load_catalog.return_value = sample_catalog

        parser = DbtManifestParser(manifest_file)
        om_client = Mock()
        # First sync succeeds, second fails
        om_client.create_or_update_table.side_effect = [
            {"id": "1"},
            Exception("Sync error"),
        ]

        stats = parser.sync_to_openmetadata(om_client, schema_name="bronze")

        assert stats["created"] == 1
        assert stats["failed"] == 1
