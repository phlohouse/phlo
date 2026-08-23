"""Tests for Nessie table scanner.

Covers namespace and table listing over the Nessie REST API, table
metadata retrieval, conversion into OpenMetadata table models, sync with
namespace include/exclude filtering, per-table failure accounting, and
request error propagation.
"""

from unittest.mock import Mock, patch

import pytest
import requests
from phlo_nessie.catalog_scanner import NessieTableScanner
from phlo_openmetadata.nessie_sync import (
    nessie_table_metadata_to_openmetadata_table,
    sync_nessie_tables_to_openmetadata,
)


@pytest.fixture
def nessie_scanner():
    """Create Nessie scanner for testing."""
    return NessieTableScanner(nessie_uri="http://nessie:19120/iceberg")


class TestNessieTableScanner:
    """Tests for NessieTableScanner."""

    def test_scanner_initialization(self, nessie_scanner):
        """Test scanner initialization."""
        assert nessie_scanner.nessie_uri == "http://nessie:19120/iceberg"
        assert nessie_scanner.timeout_seconds == 30

    def test_base_uri_trailing_slash_removed(self):
        """Test that trailing slash is removed from base URI."""
        scanner = NessieTableScanner(nessie_uri="http://nessie:19120/iceberg/")
        assert scanner.nessie_uri == "http://nessie:19120/iceberg"

    @patch("phlo_nessie.catalog_scanner.requests.request")
    def test_list_namespaces(self, mock_request, nessie_scanner):
        """Test listing namespaces."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "namespaces": [
                ["bronze"],
                ["silver"],
                ["gold"],
            ]
        }
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        result = nessie_scanner.list_namespaces()

        assert len(result) == 3
        assert result[0]["namespace"] == ["bronze"]

    @patch("phlo_nessie.catalog_scanner.requests.request")
    def test_list_tables_in_namespace(self, mock_request, nessie_scanner):
        """Test listing tables in a namespace."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "identifiers": [
                {"namespace": ["bronze"], "name": "glucose_entries"},
                {"namespace": ["bronze"], "name": "glucose_readings"},
            ]
        }
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        result = nessie_scanner.list_tables_in_namespace("bronze")

        assert len(result) == 2
        assert result[0]["name"] == "glucose_entries"

    @patch("phlo_nessie.catalog_scanner.requests.request")
    def test_list_tables_with_list_namespace(self, mock_request, nessie_scanner):
        """Test listing tables with namespace as list."""
        mock_response = Mock()
        mock_response.json.return_value = {"identifiers": []}
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        nessie_scanner.list_tables_in_namespace(["bronze", "sub"])

        # Check that namespace was joined with dots
        call_args = mock_request.call_args
        assert "/v1/namespaces/bronze.sub/tables" in str(call_args)

    @patch("phlo_nessie.catalog_scanner.requests.request")
    def test_get_table_metadata(self, mock_request, nessie_scanner):
        """Test getting table metadata."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "metadata": {
                "schemas": [
                    {
                        "schema-id": 0,
                        "fields": [
                            {"name": "id", "type": "long"},
                            {"name": "timestamp", "type": "timestamp"},
                            {"name": "value", "type": "double"},
                        ],
                    }
                ],
                "current-schema-id": 0,
                "location": "s3://lake/warehouse/bronze/glucose_entries",
            }
        }
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        result = nessie_scanner.get_table_metadata("bronze", "glucose_entries")

        assert result["name"] == "glucose_entries"
        assert len(result["schema"]["fields"]) == 3

    @patch("phlo_nessie.catalog_scanner.requests.request")
    def test_get_table_metadata_not_found(self, mock_request, nessie_scanner):
        """Test getting non-existent table metadata."""
        mock_response = Mock()
        mock_response.status_code = 404
        http_error = requests.HTTPError("Not found")
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        result = nessie_scanner.get_table_metadata("bronze", "nonexistent")

        assert result is None

    def test_extract_openmetadata_table(self, nessie_scanner):
        """Test extracting OpenMetadata table from Nessie metadata."""
        table_metadata = {
            "name": "glucose_entries",
            "doc": "Glucose sensor readings",
            "schema": {
                "fields": [
                    {"name": "id", "type": "long"},
                    {"name": "value", "type": "double"},
                ]
            },
            "properties": {"location": "s3://lake/warehouse/bronze/glucose_entries"},
        }

        om_table = nessie_table_metadata_to_openmetadata_table(table_metadata)

        assert om_table.name == "glucose_entries"
        assert om_table.description == "Glucose sensor readings"
        assert om_table.columns is not None
        assert len(om_table.columns) == 2
        assert om_table.columns[0].name == "id"
        assert om_table.columns[0].dataType == "BIGINT"
        assert om_table.location == "s3://lake/warehouse/bronze/glucose_entries"

    @patch.object(NessieTableScanner, "get_table_metadata")
    @patch.object(NessieTableScanner, "scan_all_tables")
    def test_sync_to_openmetadata(
        self,
        mock_scan,
        mock_get_meta,
        nessie_scanner,
    ):
        """Test syncing tables to OpenMetadata."""
        # Mock scan results
        mock_scan.return_value = {
            "bronze": [
                {"name": "glucose_entries", "schema": {"fields": []}},
                {"name": "weather_data", "schema": {"fields": []}},
            ]
        }

        mock_get_meta.return_value = {"name": "x", "schema": {"fields": []}}

        # Mock OpenMetadata client
        om_client = Mock()
        om_client.create_or_update_table.return_value = {"id": "123"}

        # Perform sync
        stats = sync_nessie_tables_to_openmetadata(nessie_scanner, om_client)

        assert stats["created"] == 2
        assert stats["failed"] == 0
        assert om_client.create_or_update_table.call_count == 2

    @patch.object(NessieTableScanner, "get_table_metadata")
    @patch.object(NessieTableScanner, "scan_all_tables")
    def test_sync_with_namespace_filtering(self, mock_scan, mock_get_meta, nessie_scanner):
        """Test syncing with namespace filtering."""
        mock_scan.return_value = {
            "bronze": [{"name": "table1", "schema": {"fields": []}}],
            "silver": [{"name": "table2", "schema": {"fields": []}}],
            "gold": [{"name": "table3", "schema": {"fields": []}}],
        }

        om_client = Mock()
        mock_get_meta.return_value = {"name": "x", "schema": {"fields": []}}

        # Include only bronze and silver
        sync_nessie_tables_to_openmetadata(
            nessie_scanner,
            om_client,
            include_namespaces=["bronze", "silver"],
        )

        # Should have called create_or_update_table twice (bronze + silver)
        assert om_client.create_or_update_table.call_count == 2

    @patch.object(NessieTableScanner, "get_table_metadata")
    @patch.object(NessieTableScanner, "scan_all_tables")
    def test_sync_with_namespace_exclusion(self, mock_scan, mock_get_meta, nessie_scanner):
        """Test syncing with namespace exclusion."""
        mock_scan.return_value = {
            "bronze": [{"name": "table1", "schema": {"fields": []}}],
            "silver": [{"name": "table2", "schema": {"fields": []}}],
            "gold": [{"name": "table3", "schema": {"fields": []}}],
        }

        om_client = Mock()
        mock_get_meta.return_value = {"name": "x", "schema": {"fields": []}}

        # Exclude gold
        sync_nessie_tables_to_openmetadata(
            nessie_scanner,
            om_client,
            exclude_namespaces=["gold"],
        )

        # Should have called create_or_update_table twice (bronze + silver)
        assert om_client.create_or_update_table.call_count == 2

    @patch("phlo_nessie.catalog_scanner.requests.request")
    def test_request_error_handling(self, mock_request, nessie_scanner):
        """Test error handling in _request."""
        mock_request.side_effect = requests.ConnectionError("Connection failed")

        with pytest.raises(requests.ConnectionError):
            nessie_scanner._request("GET", "/namespaces")

    @patch.object(NessieTableScanner, "get_table_metadata")
    @patch.object(NessieTableScanner, "scan_all_tables")
    def test_sync_partial_failure(self, mock_scan, mock_get_meta, nessie_scanner):
        """Test sync with partial failures."""
        mock_scan.return_value = {
            "bronze": [
                {"name": "table1", "schema": {"fields": []}},
                {"name": "table2", "schema": {"fields": []}},
            ]
        }

        om_client = Mock()
        # First call succeeds, second fails
        om_client.create_or_update_table.side_effect = [
            {"id": "1"},
            Exception("Sync error"),
        ]

        mock_get_meta.return_value = {"name": "x", "schema": {"fields": []}}
        stats = sync_nessie_tables_to_openmetadata(nessie_scanner, om_client)

        assert stats["created"] == 1
        assert stats["failed"] == 1
