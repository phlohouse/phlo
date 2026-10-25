"""CORS regression tests for the phlo-api service.

Each case issues a preflight OPTIONS request against /health and asserts that
the Observatory browser origins are accepted as allowed origins.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phlo_api.main import app


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:3001", "http://127.0.0.1:3002"],
    ids=["docker", "parallel-dev"],
)
def test_cors_allows_observatory_origin(origin: str) -> None:
    response = TestClient(app).options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
