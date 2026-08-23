"""CORS regression tests for the phlo-api service.

Each case issues a preflight OPTIONS request against /health and asserts that
the Observatory browser origins are accepted as allowed origins.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from phlo_api.main import app


def test_cors_allows_docker_observatory_origin():
    """Docker Observatory can fetch the API directly from the browser."""
    response = TestClient(app).options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:3001",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3001"


def test_cors_allows_local_observatory_dev_ports():
    """Parallel Observatory QA/dev servers can fetch the API directly."""
    response = TestClient(app).options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:3002",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3002"
