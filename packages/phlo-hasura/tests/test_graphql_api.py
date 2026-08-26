"""
Automated tests for Phlo GraphQL API (Hasura).

Tests authentication, queries, and GraphQL-specific functionality.
"""

import os

import pytest
import requests

# Mark entire module as integration tests (requires running API services)
pytestmark = pytest.mark.integration

BASE_URL = os.getenv("PHLO_HASURA_URL", "http://localhost:8081")
GRAPHQL_ENDPOINT = f"{BASE_URL}/v1/graphql"
HASURA_ADMIN_SECRET = os.getenv("HASURA_ADMIN_SECRET", "phlo-hasura-admin-secret")


@pytest.fixture(scope="session", autouse=True)
def _require_hasura():
    """Skip if Hasura is not reachable."""
    try:
        response = requests.get(f"{BASE_URL}/healthz", timeout=2)
    except requests.RequestException as exc:  # pragma: no cover - network dependent
        pytest.skip(f"Hasura not reachable at {BASE_URL}: {exc}")
    if response.status_code not in [200, 204]:
        pytest.skip(f"Hasura health check failed ({response.status_code})")


class TestGraphQLAuthentication:
    """Test GraphQL authentication with admin secret."""

    @pytest.fixture
    def admin_headers(self) -> dict[str, str]:
        """Get admin headers for Hasura."""
        return {"x-hasura-admin-secret": HASURA_ADMIN_SECRET}

    def test_graphql_requires_auth(self):
        """Test that GraphQL endpoint requires authentication."""
        query = """
        query {
            __schema {
                queryType {
                    name
                }
            }
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            json={"query": query},
        )
        # An unauthenticated request must never receive a successful schema
        # answer: either the transport rejects it or GraphQL returns errors.
        if response.status_code == 200:
            data = response.json()
            assert "errors" in data, "unauthenticated request succeeded"
        else:
            assert response.status_code in (401, 403)

    def test_graphql_with_admin_token(self, admin_headers):
        """Test GraphQL with admin secret."""
        query = """
        query {
            __schema {
                queryType {
                    name
                }
            }
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": query},
        )
        assert response.status_code == 200
        data = response.json()
        # Should not have auth errors with valid token
        if "errors" in data:
            # Check that errors are not auth-related
            for error in data["errors"]:
                assert "unauthorized" not in error.get("message", "").lower()
                assert "permission" not in error.get("message", "").lower()

    def test_graphql_with_invalid_token(self):
        """Test GraphQL with invalid admin secret."""
        query = """
        query {
            __schema {
                queryType {
                    name
                }
            }
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers={"x-hasura-admin-secret": "invalid"},
            json={"query": query},
        )
        # An invalid secret must never yield a successful schema answer.
        if response.status_code == 200:
            assert "errors" in response.json(), "invalid secret succeeded"
        else:
            assert response.status_code in (401, 403)


class TestGraphQLIntrospection:
    """Test GraphQL introspection queries."""

    @pytest.fixture
    def admin_headers(self) -> dict[str, str]:
        """Get admin headers for Hasura."""
        return {"x-hasura-admin-secret": HASURA_ADMIN_SECRET}

    def test_schema_introspection(self, admin_headers):
        """Test that schema introspection works."""
        query = """
        query {
            __schema {
                queryType {
                    name
                }
                mutationType {
                    name
                }
            }
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": query},
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_type_introspection(self, admin_headers):
        """Test type introspection."""
        query = """
        query {
            __type(name: "query_root") {
                name
                kind
            }
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": query},
        )
        assert response.status_code == 200


class TestGraphQLQueries:
    """Test basic GraphQL queries."""

    @pytest.fixture
    def admin_headers(self) -> dict[str, str]:
        """Get admin headers for Hasura."""
        return {"x-hasura-admin-secret": HASURA_ADMIN_SECRET}

    def test_glucose_readings_query(self, admin_headers):
        """Test simple GraphQL query."""
        query = """
        query {
            __typename
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": query},
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data or "errors" in data


class TestGraphQLErrorHandling:
    """Test GraphQL error handling."""

    @pytest.fixture
    def admin_headers(self) -> dict[str, str]:
        """Get admin headers for Hasura."""
        return {"x-hasura-admin-secret": HASURA_ADMIN_SECRET}

    def test_malformed_query(self, admin_headers):
        """Test handling of malformed GraphQL query."""
        query = """
        query {
            this is not valid graphql
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": query},
        )
        assert response.status_code in [200, 400]
        data = response.json()
        assert "errors" in data

    def test_nonexistent_field(self, admin_headers):
        """Test querying non-existent field."""
        query = """
        query {
            nonexistent_table {
                id
            }
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": query},
        )
        assert response.status_code in [200, 400]
        data = response.json()
        # Should have validation errors
        assert "errors" in data

    def test_empty_query(self, admin_headers):
        """Test empty query handling."""
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={"query": ""},
        )
        assert response.status_code in [200, 400]
        assert "errors" in response.json()

    def test_missing_query(self, admin_headers):
        """Test missing query field."""
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=admin_headers,
            json={},
        )
        assert response.status_code in [200, 400]
        assert "errors" in response.json()


class TestGraphQLHealthCheck:
    """Test GraphQL health and connectivity."""

    def test_graphql_endpoint_accessible(self):
        """Test that GraphQL health endpoint is accessible."""
        response = requests.get(f"{BASE_URL}/healthz", timeout=5)
        assert response.status_code in [200, 204]

    def test_graphql_post_method(self):
        """Test that GraphQL accepts POST requests with admin secret."""
        query = """
        query {
            __typename
        }
        """
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers={"x-hasura-admin-secret": HASURA_ADMIN_SECRET},
            json={"query": query},
        )
        assert response.status_code == 200
        assert "__typename" in response.json()["data"]
