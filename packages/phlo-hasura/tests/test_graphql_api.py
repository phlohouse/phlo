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
        assert "errors" not in data, data
        assert data["data"]["__schema"]["queryType"]["name"] == "query_root"

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
        assert data["data"]["__schema"]["queryType"]["name"] == "query_root"
        assert data["data"]["__schema"]["mutationType"]["name"] == "mutation_root"

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
        assert response.json()["data"]["__type"] == {"name": "query_root", "kind": "OBJECT"}


class TestGraphQLQueries:
    """Test basic GraphQL queries."""

    @pytest.fixture
    def admin_headers(self) -> dict[str, str]:
        """Get admin headers for Hasura."""
        return {"x-hasura-admin-secret": HASURA_ADMIN_SECRET}

    def test_typename_query(self, admin_headers):
        """Test the query root answers with its own type name."""
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
        assert response.json()["data"]["__typename"] == "query_root"


class TestGraphQLConnectivity:
    """Test GraphQL connectivity."""

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
