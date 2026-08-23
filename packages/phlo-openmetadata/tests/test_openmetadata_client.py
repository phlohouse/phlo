"""Tests for the OpenMetadata REST API client.

Covers the column and table dataclasses (dict rendering drops None values),
and OpenMetadataClient behaviour against mocked HTTP requests, including
table upserts and retry handling.
"""

from unittest.mock import Mock, patch

import pytest
import requests
from phlo_openmetadata.openmetadata import (
    OpenMetadataClient,
    OpenMetadataColumn,
    OpenMetadataTable,
)


@pytest.fixture
def om_client():
    """Create OpenMetadata client for testing."""
    return OpenMetadataClient(
        base_url="http://openmetadata:8585/api",
        username="admin",
        password="admin",
        verify_ssl=False,
        timeout=10,
    )


class TestOpenMetadataColumn:
    """Tests for OpenMetadataColumn dataclass."""

    def test_column_creation(self):
        """Test creating a column."""
        col = OpenMetadataColumn(
            name="user_id",
            dataType="STRING",
            description="Unique user identifier",
        )

        assert col.name == "user_id"
        assert col.dataType == "STRING"
        assert col.description == "Unique user identifier"

    def test_column_to_dict(self):
        """Test converting column to dict."""
        col = OpenMetadataColumn(
            name="user_id",
            dataType="STRING",
            description="User ID",
            ordinalPosition=0,
        )

        col_dict = col.to_dict()

        assert col_dict["name"] == "user_id"
        assert col_dict["dataType"] == "STRING"
        assert col_dict["description"] == "User ID"
        assert col_dict["ordinalPosition"] == 0
        # None values should be excluded
        assert "displayName" not in col_dict

    def test_column_to_dict_excludes_none(self):
        """Test that None values are excluded from dict."""
        col = OpenMetadataColumn(
            name="col",
            dataType="INT",
            description=None,
        )

        col_dict = col.to_dict()

        assert "description" not in col_dict


class TestOpenMetadataTable:
    """Tests for OpenMetadataTable dataclass."""

    def test_table_creation(self):
        """Test creating a table."""
        col = OpenMetadataColumn(name="id", dataType="INT")
        table = OpenMetadataTable(
            name="users",
            description="User data",
            columns=[col],
            location="s3://bucket/users",
        )

        assert table.name == "users"
        assert table.description == "User data"
        assert table.columns is not None
        assert len(table.columns) == 1
        assert table.location == "s3://bucket/users"

    def test_table_to_dict(self):
        """Test converting table to dict."""
        col = OpenMetadataColumn(name="id", dataType="INT")
        table = OpenMetadataTable(
            name="users",
            description="User data",
            columns=[col],
        )

        table_dict = table.to_dict()

        assert table_dict["name"] == "users"
        assert table_dict["description"] == "User data"
        assert len(table_dict["columns"]) == 1
        assert table_dict["columns"][0]["name"] == "id"


class TestOpenMetadataClient:
    """Tests for OpenMetadataClient."""

    def test_client_initialization(self, om_client):
        """Test client initialization."""
        assert om_client.base_url == "http://openmetadata:8585/api"
        assert om_client.username == "admin"
        assert om_client.password == "admin"
        assert om_client.timeout == 10

    def test_base_url_trailing_slash_removed(self):
        """Test that trailing slash is removed from base URL."""
        client = OpenMetadataClient(
            base_url="http://openmetadata:8585/api/",
            username="admin",
            password="admin",
        )

        assert client.base_url == "http://openmetadata:8585/api"

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_health_check_success(self, mock_request, om_client):
        """Test successful health check."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        assert om_client.health_check() is True

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_health_check_failure(self, mock_request, om_client):
        """Test failed health check."""
        mock_request.side_effect = requests.ConnectionError("Connection failed")

        assert om_client.health_check() is False

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_request_success(self, mock_request, om_client):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"id": "123", "name": "test"}}
        mock_response.text = "data"
        mock_request.return_value = mock_response

        result = om_client._request("GET", "/v1/tables")

        assert result == {"data": {"id": "123", "name": "test"}}
        mock_request.assert_called_once()

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_request_empty_response(self, mock_request, om_client):
        """Test handling empty response."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_response.text = ""
        mock_request.return_value = mock_response

        result = om_client._request("DELETE", "/v1/tables/123")

        assert result == {}

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_request_http_error(self, mock_request, om_client):
        """Test handling HTTP error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError("Not found")
        mock_request.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            om_client._request("GET", "/v1/tables/nonexistent")

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_create_or_update_table_create(self, mock_request, om_client):
        """Test creating a new table."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "123", "name": "users"}
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        col = OpenMetadataColumn(name="id", dataType="INT")
        table = OpenMetadataTable(name="users", columns=[col])

        result = om_client.create_or_update_table("public", table)

        assert result == {"id": "123", "name": "users"}
        mock_request.assert_called_once()

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_get_table_found(self, mock_request, om_client):
        """Test getting an existing table."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123", "name": "users"}
        mock_request.return_value = mock_response

        result = om_client.get_table("public.users")

        assert result == {"id": "123", "name": "users"}

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_get_table_not_found(self, mock_request, om_client):
        """Test getting a non-existent table."""
        mock_response = Mock()
        mock_response.status_code = 404
        http_error = requests.HTTPError("Not found")
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error
        mock_request.return_value = mock_response

        result = om_client.get_table("public.nonexistent")

        assert result is None

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_create_lineage(self, mock_request, om_client):
        """Test creating a lineage edge."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "edges": [{"fromEntity": "source", "toEntity": "target"}]
        }
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        result = om_client.create_lineage(
            "public.source",
            "public.target",
            description="Data flows from source to target",
        )

        assert "edges" in result
        assert mock_request.call_count == 3
        assert mock_request.call_args_list[-1].kwargs["method"] == "PUT"

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_list_databases(self, mock_request, om_client):
        """Test listing databases."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "1", "name": "postgres"},
                {"id": "2", "name": "iceberg"},
            ]
        }
        mock_response.text = "{}"
        mock_request.return_value = mock_response

        result = om_client.list_databases()

        assert len(result) == 2
        assert result[0]["name"] == "postgres"

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_list_databases_error_handling(self, mock_request, om_client):
        """Test error handling in list_databases."""
        mock_request.side_effect = requests.ConnectionError("Connection failed")

        result = om_client.list_databases()

        assert result == []

    @patch("phlo_openmetadata.openmetadata.requests.Session.request")
    def test_add_owner(self, mock_request, om_client):
        """Test adding an owner to an entity."""
        # First call gets the entity
        mock_response_get = Mock()
        mock_response_get.status_code = 200
        mock_response_get.json.return_value = {"id": "123", "name": "users"}
        mock_response_get.text = "{}"

        # Second call updates the entity
        mock_response_update = Mock()
        mock_response_update.status_code = 200
        mock_response_update.json.return_value = {
            "id": "123",
            "name": "users",
            "owner": {"name": "alice", "type": "user"},
        }
        mock_response_update.text = "{}"

        mock_request.side_effect = [mock_response_get, mock_response_update]

        result = om_client.add_owner("public.users", "alice")

        assert result["owner"]["name"] == "alice"
        assert mock_request.call_count == 2
