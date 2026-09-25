"""Contract tests for typed phlo-api error reporting.

Mounted routers report failures through HTTP status codes and the shared
error envelope ({"error": {"code", "message"}}) instead of HTTP 200 error
payloads. These tests walk the mounted app for `| dict` response-model
unions and pin per-route failure behavior for the sites converted away from
`return {"error": ...}`.
"""

from __future__ import annotations

import json
import types
import typing

import httpx
import pytest
from fastapi.routing import APIRoute

from phlo_api.api import maintenance, observability
from phlo_api.errors import BackendUnavailableError
from phlo_api.main import app
from phlo_api.observatory_api import contributing, loki

from security_test_support import authenticated_client

_SECRET = "secret-internal-path-/srv/phlo"


def _raises_runtime(*args: object, **kwargs: object) -> typing.NoReturn:
    raise RuntimeError(_SECRET)


def _response_model_unions_in_bare_dict(model: object) -> bool:
    if typing.get_origin(model) not in (typing.Union, types.UnionType):
        return False
    return any(arg is dict or typing.get_origin(arg) is dict for arg in typing.get_args(model))


def test_no_mounted_route_response_model_unions_in_a_bare_dict() -> None:
    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        model = route.response_model
        if model is not None and _response_model_unions_in_bare_dict(model):
            offenders.append(f"{sorted(route.methods)} {route.path}")
    assert offenders == []


_OBSERVABILITY_PATHS = [
    "/api/observability/health",
    "/api/observability/services",
    "/api/observability/metrics",
    "/api/observability/alerts",
    "/api/observability/dashboards",
    "/api/observability/links/logs",
    "/api/observability/links/metrics",
    "/api/observability/traces/runs/run-1",
    "/api/observability/traces",
]


@pytest.mark.parametrize("path", _OBSERVABILITY_PATHS)
def test_observability_backend_failure_returns_typed_503(path: str, monkeypatch) -> None:
    """A broken backend yields the 503 envelope without exception text."""
    monkeypatch.setattr(observability, "_resolve_observability_backend", _raises_runtime)

    response = authenticated_client("admin").get(path)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"
    assert _SECRET not in response.text


@pytest.mark.parametrize(
    "path",
    ["/api/maintenance/status", "/api/maintenance/metrics"],
)
def test_maintenance_backend_failure_returns_typed_503(path: str, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_resolve_maintenance_read_model", _raises_runtime)

    response = authenticated_client("admin").get(path)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"
    assert _SECRET not in response.text


_LOKI_PATHS = [
    "/api/loki/query?start=2024-01-01T00:00:00Z&end=2024-01-01T01:00:00Z",
    "/api/loki/runs/run-1",
    "/api/loki/assets/raw/orders",
    "/api/loki/labels",
]


@pytest.mark.parametrize("path", _LOKI_PATHS)
def test_loki_backend_failure_returns_typed_503(path: str, monkeypatch) -> None:
    """A Loki that cannot be configured yields the 503 envelope, not internals."""
    monkeypatch.setattr(loki, "resolve_loki_url", _raises_runtime)

    response = authenticated_client("admin").get(path)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"
    assert _SECRET not in response.text


def test_loki_connection_failure_returns_typed_503(monkeypatch) -> None:
    monkeypatch.setattr(loki, "resolve_loki_url", _raises_runtime)

    response = authenticated_client("admin").get("/api/loki/connection")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"
    assert _SECRET not in response.text


def test_loki_connection_upstream_error_returns_typed_502(monkeypatch) -> None:
    async_client = httpx.AsyncClient
    monkeypatch.setattr(loki, "resolve_loki_url", lambda: "http://loki.internal:3100")
    monkeypatch.setattr(
        loki.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, text=_SECRET, request=request)
            ),
            **kwargs,
        ),
    )

    response = authenticated_client("admin").get("/api/loki/connection")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "bad_gateway"
    assert _SECRET not in response.text


def test_loki_stream_initial_failure_returns_typed_503(monkeypatch) -> None:
    async def fail_query(**kwargs: object) -> typing.NoReturn:
        raise BackendUnavailableError("Loki backend is unavailable.")

    monkeypatch.setattr(loki, "fetch_run_log_entries", fail_query)

    response = authenticated_client("admin").get("/api/loki/runs/run-1/stream")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"


def test_loki_stream_later_failure_uses_safe_sse_error(monkeypatch) -> None:
    calls = 0

    async def query(**kwargs: object) -> loki.LogQueryResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BackendUnavailableError("Loki backend is unavailable.") from RuntimeError(_SECRET)
        return loki.LogQueryResult(
            entries=[loki.LogEntry(timestamp="1", level="info", message="first", metadata={})],
            has_more=False,
        )

    monkeypatch.setattr(loki, "fetch_run_log_entries", query)

    response = authenticated_client("admin").get(
        "/api/loki/runs/run-1/stream", params={"interval_seconds": "0.25"}
    )

    assert response.status_code == 200
    assert calls == 2
    assert "event: log\n" in response.text
    assert "event: error\n" in response.text
    error_event = response.text.split("event: error\ndata: ", 1)[1].split("\n", 1)[0]
    assert json.loads(error_event)["error"]["code"] == "backend_unavailable"
    assert _SECRET not in response.text


_CONTRIBUTING_BODY = {
    "downstream_asset_key": "marts/fct_daily_metrics",
    "upstream_asset_key": "bronze/raw_events",
    "row_data": {"date": "2024-01-15"},
}


@pytest.mark.parametrize(
    "path",
    [
        "/api/observatory/contributing-rows/query",
        "/api/observatory/contributing-rows/page",
    ],
)
def test_contributing_rows_backend_failure_returns_typed_503(path: str, monkeypatch) -> None:
    monkeypatch.setattr(contributing, "resolve_default_catalog", _raises_runtime)

    response = authenticated_client("admin").post(path, json=_CONTRIBUTING_BODY)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"
    assert _SECRET not in response.text


def test_contributing_rows_unrelated_pair_returns_typed_400(monkeypatch) -> None:
    monkeypatch.setattr(contributing, "resolve_default_catalog", lambda: "iceberg")

    response = authenticated_client("admin").post(
        "/api/observatory/contributing-rows/query",
        json=_CONTRIBUTING_BODY,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unrelated_asset_pair"


def test_phlo_api_error_maps_status_code_and_message() -> None:
    exc = BackendUnavailableError("Observability backend is unavailable.")

    assert exc.status_code == 503
    assert exc.code == "backend_unavailable"
    from phlo_api.errors import error_envelope

    assert error_envelope(exc) == {
        "error": {"code": "backend_unavailable", "message": "Observability backend is unavailable."}
    }
