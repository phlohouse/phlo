"""Tests for lineage extraction and publishing.

Covers building OpenMetadataLineageGraph from a dbt manifest and Iceberg
tables, publish edge construction including skip-on-error behavior, impact
analysis, JSON/DOT/Mermaid export, and FQN normalization.
"""

from unittest.mock import Mock

import pytest
from phlo_openmetadata.graph import OpenMetadataLineageGraph
from phlo_openmetadata.lineage import LineageExtractor


@pytest.fixture
def sample_manifest():
    """Sample dbt manifest for testing."""
    return {
        "nodes": {
            "model.project.stg_glucose": {
                "name": "stg_glucose",
                "depends_on": {"nodes": ["source.project.nightscout.glucose"]},
            },
            "model.project.fct_glucose": {
                "name": "fct_glucose",
                "depends_on": {"nodes": ["model.project.stg_glucose"]},
            },
            "model.project.mrt_glucose": {
                "name": "mrt_glucose",
                "schema": "marts",
                "depends_on": {"nodes": ["model.project.fct_glucose"]},
            },
        },
        "sources": {
            "source.project.nightscout.glucose": {
                "source_name": "nightscout",
                "name": "glucose",
            }
        },
    }


@pytest.fixture
def nessie_tables():
    """Sample Nessie tables."""
    return {
        "raw": [
            {"name": "glucose_entries"},
            {"name": "weather_data"},
        ],
        "processed": [
            {"name": "glucose_processed"},
        ],
    }


class TestLineageExtractor:
    """Tests for LineageExtractor."""

    def test_extractor_initialization(self):
        """Test extractor initialization."""
        extractor = LineageExtractor()

        assert isinstance(extractor.graph, OpenMetadataLineageGraph)
        assert len(extractor.graph.assets) == 0

    def test_extract_from_dbt_manifest(self, sample_manifest):
        """Test extracting lineage from dbt manifest."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        # Check assets were added
        assert "stg_glucose" in extractor.graph.assets
        assert "fct_glucose" in extractor.graph.assets
        assert "mrt_glucose" in extractor.graph.assets
        assert "nightscout.glucose" in extractor.graph.assets

        # Check edges
        assert "fct_glucose" in extractor.graph.edges.get("stg_glucose", [])
        assert "mrt_glucose" in extractor.graph.edges.get("fct_glucose", [])

    def test_extract_from_dbt_manifest_asset_types(self, sample_manifest):
        """Test that asset types are set correctly."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        # Models should be type "transform"
        assert extractor.graph.assets["stg_glucose"].asset_type == "transform"
        assert extractor.graph.assets["fct_glucose"].asset_type == "transform"

        # Sources should be type "ingestion"
        assert extractor.graph.assets["nightscout.glucose"].asset_type == "ingestion"

    def test_extract_from_iceberg(self, nessie_tables):
        """Test extracting Iceberg tables."""
        extractor = LineageExtractor()
        extractor.extract_from_iceberg(nessie_tables)

        # Check assets were added
        assert "raw.glucose_entries" in extractor.graph.assets
        assert "raw.weather_data" in extractor.graph.assets
        assert "processed.glucose_processed" in extractor.graph.assets

        # Check asset types
        for asset in extractor.graph.assets.values():
            assert asset.asset_type == "ingestion"

    def test_build_publishing_lineage(self, sample_manifest):
        """Test building publishing lineage."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        lineage = extractor.build_publishing_lineage(sample_manifest, postgres_schema="marts")

        # Check that we have lineage from source to published table
        assert "nightscout.glucose" in lineage
        assert "mrt_glucose" in lineage["nightscout.glucose"]

    def test_build_publishing_lineage_no_published_models(self):
        """Test when no published models exist."""
        extractor = LineageExtractor()
        manifest = {
            "nodes": {
                "model.project.stg_glucose": {
                    "name": "stg_glucose",
                    "schema": "bronze",
                    "depends_on": {"nodes": []},
                }
            },
            "sources": {},
        }

        lineage = extractor.build_publishing_lineage(manifest, postgres_schema="marts")

        # No lineage should be returned since no models in marts schema
        assert len(lineage) == 0

    def test_publish_to_openmetadata(self, sample_manifest):
        """Test publishing lineage to OpenMetadata."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        om_client = Mock()
        om_client.create_lineage.return_value = {"id": "lineage_123"}

        stats = extractor.publish_to_openmetadata(om_client)

        assert stats["edges_published"] == 3
        assert om_client.create_lineage.called

    def test_publish_to_openmetadata_skip_edges(self, sample_manifest):
        """Test publishing without edges."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        om_client = Mock()

        stats = extractor.publish_to_openmetadata(om_client, include_edges=False)

        assert stats["edges_published"] == 0
        assert not om_client.create_lineage.called

    def test_publish_to_openmetadata_with_errors(self, sample_manifest):
        """Test publishing with some failures."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        om_client = Mock()
        om_client.create_lineage.side_effect = [
            {"id": "lineage_1"},
            Exception("API error"),
            {"id": "lineage_3"},
        ]

        stats = extractor.publish_to_openmetadata(om_client)

        assert stats["edges_published"] == 2
        assert stats["failed"] == 1

    def test_get_impact_analysis(self, sample_manifest):
        """Test impact analysis."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        impact = extractor.get_impact_analysis("stg_glucose")

        # Changing stg_glucose should impact fct_glucose and mrt_glucose
        assert "fct_glucose" in impact["affected_assets"]
        assert "mrt_glucose" in impact["affected_assets"]
        assert impact["total_affected"] == 2

    def test_get_impact_analysis_no_downstream(self, sample_manifest):
        """Test impact analysis for asset with no downstream."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        # Leaf node has no downstream impact
        impact = extractor.get_impact_analysis("mrt_glucose")

        assert impact["total_affected"] == 0

    def test_export_lineage_json(self, sample_manifest):
        """Test exporting lineage as JSON."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        json_output = extractor.export_lineage(format_type="json")

        assert isinstance(json_output, str)
        assert "stg_glucose" in json_output
        assert "assets" in json_output

    def test_export_lineage_dot(self, sample_manifest):
        """DOT export must carry every node and each manifest edge exactly once."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        dot_output = extractor.export_lineage(format_type="dot")

        assert dot_output.startswith("digraph")
        for model in ("nightscout.glucose", "stg_glucose", "fct_glucose", "mrt_glucose"):
            assert model in dot_output
        assert dot_output.count("->") == 3

    def test_export_lineage_mermaid(self, sample_manifest):
        """Mermaid export must carry every node and each manifest edge exactly once."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        mermaid_output = extractor.export_lineage(format_type="mermaid")

        for model in ("nightscout.glucose", "stg_glucose", "fct_glucose", "mrt_glucose"):
            assert model in mermaid_output
        assert mermaid_output.count("-->") == 3

    def test_export_lineage_invalid_format(self, sample_manifest):
        """Test exporting with invalid format."""
        extractor = LineageExtractor()
        extractor.extract_from_dbt_manifest(sample_manifest)

        with pytest.raises(ValueError):
            extractor.export_lineage(format_type="invalid")

    def test_normalize_fqn(self):
        """Test FQN normalization."""
        # Already qualified
        assert LineageExtractor._normalize_fqn("schema.table") == "schema.table"

        # Unqualified
        assert LineageExtractor._normalize_fqn("table") == "default.table"

    def test_extract_from_dbt_manifest_error_handling(self):
        """Test error handling in dbt extraction."""
        extractor = LineageExtractor()

        # Extract with invalid manifest (should not raise, just log warning)
        extractor.extract_from_dbt_manifest({})

        assert len(extractor.graph.assets) == 0

    def test_extract_from_iceberg_error_handling(self):
        """Test error handling in Iceberg extraction."""
        extractor = LineageExtractor()

        # Extract with invalid data (should not raise, just log warning)
        extractor.extract_from_iceberg({})

        assert len(extractor.graph.assets) == 0
