"""Tests for concrete Observatory API surfaces.

Checks that run, governance, and capability endpoints return provider-neutral
shapes and that removed endpoints stay hard-removed.
"""

from __future__ import annotations

import pytest

from security_test_support import authenticated_client
from phlo_api.observatory_api import observatory_runs


def test_observatory_runs_adapter_normalizes_legacy_dagster_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_runs() -> list[dict[str, object]]:
        return [
            {
                "id": "run-1",
                "jobName": "materialize_all",
                "status": "SUCCESS",
                "startTime": 1.0,
                "endTime": 61.0,
                "assetKeys": [["bronze", "orders"]],
            }
        ]

    monkeypatch.setattr(observatory_runs, "_load_legacy_dagster_runs", lambda: fake_get_runs())

    runs = observatory_runs.load_runs()

    assert runs[0].id == "run-1"
    assert runs[0].name == "materialize_all"
    assert runs[0].status == "succeeded"
    assert runs[0].started_at == "1970-01-01T00:00:01+00:00"
    assert runs[0].completed_at == "1970-01-01T00:01:01+00:00"
    assert runs[0].duration_seconds == 60.0
    assert runs[0].assets[0].id == "bronze.orders"
    assert runs[0].assets[0].kind == "asset"
    assert runs[0].assets[0].label == "bronze.orders"
    assert runs[0].metadata == {"source": "dagster"}


def test_observatory_runs_endpoint_returns_provider_neutral_shape() -> None:
    response = authenticated_client("admin").get("/api/observatory/runs")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)


@pytest.mark.parametrize(
    "path",
    [
        "/api/observatory/storage",
        "/api/observatory/observability",
        "/api/observatory/apis",
        "/api/observatory/bi",
    ],
)
def test_observatory_surface_endpoints_return_provider_neutral_shape(path: str) -> None:
    response = authenticated_client("admin").get(path)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items"}
    assert isinstance(payload["items"], list)


@pytest.mark.parametrize(
    "path",
    [
        "/api/observatory/catalog",
        "/api/observatory/data-products",
        "/api/observatory/data-products/gold.revenue",
    ],
)
def test_removed_observatory_endpoints_are_hard_404s(path: str) -> None:
    response = authenticated_client("admin").get(path)

    assert response.status_code == 404


def test_observatory_governance_endpoint_returns_matrix_shape() -> None:
    response = authenticated_client("admin").get("/api/observatory/governance")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"controls", "rows", "status_counts"}
    assert isinstance(payload["controls"], list)
    assert isinstance(payload["rows"], list)
    assert isinstance(payload["status_counts"], dict)
