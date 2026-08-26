"""Comprehensive integration tests for phlo-api.

Per TEST_STRATEGY.md Level 2 (Functional):
- API Endpoints: Spin up FastAPI test client, hit endpoints, verify 200 OK
- Route Definitions: Verify all expected routes exist
- Plugin/Service Discovery: Test plugin listing endpoints
"""

from unittest.mock import patch

import pytest
from security_test_support import authenticated_client

pytestmark = pytest.mark.integration


# =============================================================================
# FastAPI App Tests
# =============================================================================


class TestFastAPIApp:
    """Test FastAPI app creation and configuration."""

    def test_app_creates(self):
        """Test that FastAPI app can be created."""
        from phlo_api.main import app

        assert app is not None
        assert app.title == "Phlo API"
        assert app.version == "0.1.0"

    def test_cors_middleware_configured(self):
        """Test that CORS middleware is configured."""
        from phlo_api.main import app

        # Check middleware is configured
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_classes

    def test_routes_registered(self):
        """Test that expected routes are registered."""
        from phlo_api.main import app

        route_paths = [r.path for r in app.routes]  # type: ignore[attr-defined]

        # Core endpoints
        assert "/health" in route_paths
        assert "/api/config" in route_paths
        assert "/api/plugins" in route_paths
        assert "/api/services" in route_paths
        assert "/api/backends" in route_paths
        assert "/api/backends/{name}" in route_paths
        assert "/api/contracts" in route_paths
        assert "/api/contracts/{table_name:path}" in route_paths


# =============================================================================
# Health Endpoint Tests
# =============================================================================


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_returns_200(self):
        """Test health endpoint returns 200 OK."""

        client = authenticated_client("admin")
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


# =============================================================================
# Config Endpoint Tests
# =============================================================================


class TestConfigEndpoint:
    """Test config endpoint."""

    def test_config_returns_dict(self):
        """Test config endpoint returns a dictionary."""

        client = authenticated_client("admin")
        response = client.get("/api/config")

        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    def test_config_with_missing_file(self):
        """Test config endpoint when phlo.yaml doesn't exist."""
        from pathlib import Path

        with patch("phlo_api.main.get_project_path", return_value=Path("/nonexistent")):
            client = authenticated_client("admin")
            response = client.get("/api/config")

            assert response.status_code == 200
            # Should return default config
            data = response.json()
            assert "name" in data

    def test_config_with_malformed_file_returns_clean_error(self, tmp_path):
        """Malformed phlo.yaml should not leak a server traceback."""

        (tmp_path / "phlo.yaml").write_text("name: [bad\n", encoding="utf-8")
        with patch("phlo_api.main.get_project_path", return_value=tmp_path):
            response = authenticated_client("admin").get("/api/config")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to read phlo.yaml"

    def test_config_with_non_mapping_file_returns_clean_error(self, tmp_path):
        """Non-object phlo.yaml should not leak a server traceback."""

        (tmp_path / "phlo.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
        with patch("phlo_api.main.get_project_path", return_value=tmp_path):
            response = authenticated_client("admin").get("/api/config")

        assert response.status_code == 500
        assert response.json()["detail"] == "phlo.yaml must contain a mapping"


# =============================================================================
# Plugins Endpoints Tests
# =============================================================================


class TestPluginsEndpoints:
    """Test plugin discovery endpoints."""

    def test_plugins_list_endpoint(self):
        """Test listing all plugins returns valid response."""

        client = authenticated_client("admin")
        response = client.get("/api/plugins")

        # Should return 200 with dict (may be empty if discovery not available)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_plugins_by_type_endpoint(self):
        """Test listing plugins by type returns valid response."""

        client = authenticated_client("admin")
        response = client.get("/api/plugins/service")

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_plugins_unknown_type_returns_404(self):
        """Test unknown plugin type returns 404."""

        client = authenticated_client("admin")
        response = client.get("/api/plugins/unknown_type_that_does_not_exist")

        assert response.status_code == 404
        assert "Unknown plugin type" in response.json()["detail"]

    def test_plugin_info_unknown_type_returns_404(self):
        """Test unknown plugin type in plugin detail returns 404."""

        client = authenticated_client("admin")
        response = client.get("/api/plugins/unknown_type_that_does_not_exist/example")

        assert response.status_code == 404
        assert "Unknown plugin family" in response.json()["detail"]


# =============================================================================
# Services Endpoints Tests
# =============================================================================


class TestServicesEndpoints:
    """Test service discovery endpoints."""

    def test_services_list_endpoint(self):
        """Test listing all services."""

        client = authenticated_client("admin")
        response = client.get("/api/services")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_services_with_discovery(self):
        """Test services endpoint returns valid data structure."""

        client = authenticated_client("admin")
        response = client.get("/api/services")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # If services returned, check structure
        if len(data) > 0:
            assert "name" in data[0] or isinstance(data[0], str)

    def test_service_info_endpoint(self):
        """Test getting specific service info returns valid response."""

        client = authenticated_client("admin")
        # Try to get a service that likely exists or doesn't
        response = client.get("/api/services/trino")

        # trino may or may not exist in local service manifests.
        assert response.status_code in (200, 404)

    def test_service_not_found_returns_404(self):
        """Test unknown service returns 404."""

        client = authenticated_client("admin")
        response = client.get("/api/services/nonexistent_service_xyz")

        assert response.status_code == 404
        assert "Service not found" in response.json()["detail"]


# =============================================================================
# Registry Endpoint Tests
# =============================================================================


class TestRegistryEndpoint:
    """Test plugin registry endpoint."""

    def test_registry_endpoint_returns_dict(self):
        """Test registry endpoint returns dictionary."""

        client = authenticated_client("admin")
        response = client.get("/api/registry")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)


# =============================================================================
# Contracts Endpoints Tests
# =============================================================================


class TestContractsEndpoints:
    """Test contract discovery endpoints."""

    def test_contracts_list_endpoint(self):
        """List contracts endpoint returns helper payload."""

        payload = [
            {
                "table_name": "raw.contract_demo",
                "asset_key": "dlt_contract_demo",
                "contract_metadata": {"owner": "platform-team", "consumers": [], "sla": None},
            }
        ]
        with patch("phlo_api.main._list_contracts", return_value=payload):
            client = authenticated_client("admin")
            response = client.get("/api/contracts")

        assert response.status_code == 200
        assert response.json() == payload

    def test_contract_detail_endpoint(self):
        """Detail endpoint returns contract for requested table."""

        payload = {
            "table_name": "raw.contract_demo",
            "asset_key": "dlt_contract_demo",
            "contract_metadata": {"owner": "platform-team", "consumers": [], "sla": None},
        }
        with patch("phlo_api.main._get_contract_by_table", return_value=payload):
            client = authenticated_client("admin")
            response = client.get("/api/contracts/raw.contract_demo")

        assert response.status_code == 200
        assert response.json() == payload

    def test_contract_detail_endpoint_not_found(self):
        """Detail endpoint returns 404 when table has no contract."""

        with patch("phlo_api.main._get_contract_by_table", return_value=None):
            client = authenticated_client("admin")
            response = client.get("/api/contracts/raw.unknown_table")

        assert response.status_code == 404
        assert "Contract not found" in response.json()["detail"]


# =============================================================================
# Service Plugin Tests
# =============================================================================


class TestAPIServicePlugin:
    """Test API service plugin."""

    def test_plugin_initializes(self):
        """Test PhloApiServicePlugin can be instantiated."""
        from phlo_api.plugin import PhloApiServicePlugin

        plugin = PhloApiServicePlugin()
        assert plugin is not None

    def test_plugin_metadata(self):
        """Test plugin metadata is defined."""
        from phlo_api.plugin import PhloApiServicePlugin

        plugin = PhloApiServicePlugin()
        assert plugin.metadata.name == "phlo-api"

    def test_service_definition_loads(self):
        """Test service definition can be loaded."""
        from phlo_api.plugin import PhloApiServicePlugin

        plugin = PhloApiServicePlugin()
        service_def = plugin.service_definition

        assert isinstance(service_def, dict)


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Test API error handling."""

    def test_404_on_unknown_route(self):
        """Test 404 returned for unknown routes."""

        client = authenticated_client("admin")
        response = client.get("/api/nonexistent/route")

        assert response.status_code == 404
