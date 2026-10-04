"""Contracts for explicit asset counts through bounded query sessions."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pytest import MonkeyPatch, raises
from types import SimpleNamespace

from phlo.capabilities import AuthPrincipal
from phlo_api import security_manifest
from phlo_api.api import v1_assets, v1_query
from phlo_api.errors import PhloApiError, error_envelope
from phlo_api.errors import BackendUnavailableError


def test_explicit_count_is_snapshot_pinned_bounded_and_zero_is_real(monkeypatch, tmp_path):
    actor = "analyst"
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod", "nessie_ref": "main"},
                "staging": {"dagster_location": "staging", "nessie_ref": "candidate"},
            }
        ),
    )
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "warehouse_prod", "nessie_ref": "main"},
                "staging": {"catalog": "warehouse_staging", "nessie_ref": "candidate"},
            }
        ),
    )
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "test-secret")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "test-secret")
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(
        v1_query,
        "get_request_principal",
        lambda _request: AuthPrincipal(subject=actor, principal_type="user", groups=()),
    )
    node = {
        "assetKey": {"path": ["orders"]},
        "metadataEntries": [{"label": "phlo/relation", "text": "analytics.orders"}],
    }

    async def asset_nodes(*_args, **_kwargs):
        return [node]

    monkeypatch.setattr(v1_assets, "_asset_nodes", asset_nodes)
    monkeypatch.setattr(
        v1_assets, "_iceberg_current_snapshot_id", lambda _table, _ref: "9007199254740993"
    )
    statements: list[tuple[str, str, int]] = []

    async def execute(sql, *, catalog, limit, **_kwargs):
        statements.append((sql, catalog, limit))
        return {
            "columns": [{"name": "row_count", "type": "varchar"}],
            "rows": [{"row_count": "0"}],
            "has_more": False,
        }

    monkeypatch.setattr(v1_query, "execute_preview", execute)
    v1_query._QUERY_SESSIONS.clear()
    app = FastAPI()
    app.include_router(v1_assets.router, prefix="/api/v1")
    app.include_router(v1_query.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def api_error(_request: Request, exc: PhloApiError):
        return JSONResponse(error_envelope(exc), status_code=exc.status_code)

    with TestClient(app) as client:
        submitted = client.post("/api/v1/assets/orders/row-count?env=prod")
        assert submitted.status_code == 202, submitted.text
        payload = submitted.json()
        assert payload["source"] == "trino_count_star"
        assert payload["nessie_ref"] == "main"
        assert payload["snapshot_id"] == "9007199254740993"
        session_id = payload["query"]["id"]
        for _ in range(20):
            result = client.get(f"/api/v1/queries/{session_id}?env=prod").json()
            if result["status"] not in {"queued", "running", "cancelling"}:
                break
            time.sleep(0.01)
        assert result["status"] == "completed"
        assert result["result"]["rows"] == [{"row_count": "0"}]
        assert statements == [
            (
                "SELECT * FROM (SELECT CAST(COUNT(*) AS VARCHAR) AS row_count FROM "
                '"warehouse_prod"."analytics"."orders" FOR VERSION AS OF '
                '9007199254740993) AS "_phlo_query_result" LIMIT 2',
                "warehouse_prod",
                1,
            )
        ]
        assert client.get(f"/api/v1/queries/{session_id}?env=staging").status_code == 404
        actor = "another-actor"
        assert client.get(f"/api/v1/queries/{session_id}?env=prod").status_code == 404
        assert (
            client.post("/api/v1/assets/orders/row-count?env=prod&nessie_ref=other").status_code
            == 400
        )
    v1_query._QUERY_SESSIONS.clear()


def test_exact_count_route_is_authorized_as_an_asset_query():
    route = security_manifest.HTTP_ROUTE_MANIFEST["v1_asset_exact_row_count"]
    assert route.action == "dataset.query"
    assert route.resource_type == "asset"
    assert route.resource_keys == ("env", "asset_id")


def test_exact_count_budget_failure_is_not_a_zero_total(monkeypatch):
    async def over_budget(*_args, **_kwargs):
        raise v1_query.PreviewLimitExceeded("budget exceeded")

    monkeypatch.setattr(v1_query, "execute_preview", over_budget)
    session = v1_query.QuerySession(
        query_id="count-budget-test",
        actor="analyst",
        env="prod",
        nessie_ref="main",
        sql="SELECT CAST(COUNT(*) AS VARCHAR) AS row_count",
        catalog="warehouse_prod",
        row_limit=1,
    )

    asyncio.run(v1_query._execute_session(session))

    assert session.status == "failed"
    assert session.result is None
    assert session.error == "Query exceeded the configured row, time, or response limit."


def test_asset_without_current_snapshot_is_unavailable_not_zero(monkeypatch: MonkeyPatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/assets/orders/row-count",
            "query_string": b"env=prod",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )
    monkeypatch.setattr(
        v1_assets, "_target", lambda *_args, **_kwargs: SimpleNamespace(nessie_ref="main")
    )

    async def asset_nodes(*_args, **_kwargs):
        return [
            {
                "assetKey": {"path": ["orders"]},
                "metadataEntries": [{"label": "phlo/relation", "text": "analytics.orders"}],
            }
        ]

    monkeypatch.setattr(v1_assets, "_asset_nodes", asset_nodes)
    monkeypatch.setattr(v1_assets, "_iceberg_current_snapshot_id", lambda *_args: None)

    with raises(BackendUnavailableError, match="no current snapshot"):
        asyncio.run(v1_assets.v1_asset_exact_row_count(request, "orders", "prod"))


def test_delayed_snapshot_lookup_times_out(monkeypatch: MonkeyPatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/assets/orders/row-count",
            "query_string": b"env=prod",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )
    monkeypatch.setattr(
        v1_assets, "_target", lambda *_args, **_kwargs: SimpleNamespace(nessie_ref="main")
    )

    async def asset_nodes(*_args, **_kwargs):
        return [
            {
                "assetKey": {"path": ["orders"]},
                "metadataEntries": [{"label": "phlo/relation", "text": "analytics.orders"}],
            }
        ]

    def delayed_snapshot_lookup(*_args):
        time.sleep(0.1)
        return "42"

    def unexpected_count(*_args, **_kwargs):
        raise AssertionError("count must not start without a timed-out snapshot lookup")

    monkeypatch.setattr(v1_assets, "_asset_nodes", asset_nodes)
    monkeypatch.setattr(v1_assets, "_iceberg_current_snapshot_id", delayed_snapshot_lookup)
    monkeypatch.setattr(v1_assets, "_EXACT_COUNT_SNAPSHOT_LOOKUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(v1_assets, "start_exact_asset_count", unexpected_count)

    with raises(BackendUnavailableError, match="snapshot identity is unavailable"):
        asyncio.run(v1_assets.v1_asset_exact_row_count(request, "orders", "prod"))


def test_snapshot_identity_preserves_the_current_iceberg_id(monkeypatch: MonkeyPatch):
    from phlo_iceberg import catalog

    snapshot_id = 9007199254740993
    table = SimpleNamespace(
        current_snapshot=lambda: SimpleNamespace(snapshot_id=snapshot_id),
    )
    monkeypatch.setattr(
        catalog,
        "get_catalog",
        lambda **kwargs: SimpleNamespace(load_table=lambda _name: table),
    )

    assert v1_assets._iceberg_current_snapshot_id("analytics.orders", "main") == str(snapshot_id)
