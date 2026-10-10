"""HTTP contracts for the ref-aware query workspace."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api.api import v1_query
from phlo_api import security_manifest
from phlo_api.errors import PhloApiError, error_envelope
from phlo_api.main import app as full_app


@pytest.fixture
def query_api(monkeypatch: pytest.MonkeyPatch, tmp_path):
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
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "test-prod-secret")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "test-staging-secret")
    monkeypatch.setenv("PHLO_V1_QUERY_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(
        v1_query,
        "get_request_principal",
        lambda _request: AuthPrincipal(subject="analyst", principal_type="user", groups=()),
    )
    v1_query._QUERY_SESSIONS.clear()
    calls: list[tuple[str, str, int]] = []

    async def execute(sql, *, catalog, disconnected, limit, on_progress=None, should_cancel=None):
        calls.append((sql, catalog, limit))
        if sql.startswith("EXPLAIN"):
            return {
                "columns": [{"name": "Query Plan", "type": "varchar"}],
                "rows": [{"Query Plan": "scan"}],
                "has_more": False,
            }
        if "information_schema.tables" in sql:
            name = "prod_schema" if catalog == "warehouse_prod" else "staging_schema"
            return {
                "columns": [
                    {"name": "table_schema", "type": "varchar"},
                    {"name": "table_name", "type": "varchar"},
                ],
                "rows": [{"table_schema": name, "table_name": "orders"}],
                "has_more": False,
            }
        return {
            "columns": [{"name": "id", "type": "bigint"}],
            "rows": [{"id": 7}],
            "has_more": False,
        }

    monkeypatch.setattr(v1_query, "execute_preview", execute)
    app = FastAPI()
    app.include_router(v1_query.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def api_error(_request: Request, exc: PhloApiError):
        return JSONResponse(error_envelope(exc), status_code=exc.status_code)

    with TestClient(app) as client:
        yield client, calls
    v1_query._QUERY_SESSIONS.clear()


def _wait_for_result(client: TestClient, query_id: str, env: str = "prod") -> dict:
    for _ in range(20):
        result = client.get(f"/api/v1/queries/{query_id}?env={env}").json()
        if result["status"] not in {"queued", "running", "cancelling"}:
            return result
        time.sleep(0.01)
    raise AssertionError("query did not settle")


def test_catalog_refs_and_engines_are_resolved_from_each_environment(query_api):
    client, _ = query_api
    prod = client.get("/api/v1/query/catalog?env=prod").json()
    staging = client.get("/api/v1/query/catalog?env=staging").json()

    assert prod["nessie_ref"] == "main"
    assert prod["catalogs"] == [
        {
            "name": "warehouse_prod",
            "schemas": [{"name": "prod_schema", "tables": ["orders"]}],
            "truncated": False,
        }
    ]
    assert staging["nessie_ref"] == "candidate"
    assert staging["catalogs"] == [
        {
            "name": "warehouse_staging",
            "schemas": [{"name": "staging_schema", "tables": ["orders"]}],
            "truncated": False,
        }
    ]
    assert client.get("/api/v1/query/refs?env=staging").json()["items"][0]["name"] == "candidate"
    assert client.get("/api/v1/query/engines?env=prod").json()["items"] == [
        {"id": "trino", "status": "configured"}
    ]
    assert client.get("/api/v1/query/catalog?env=invalid").status_code == 422


@pytest.mark.parametrize(
    "name", ["PHLO_V1_QUERY_SINGLE_REPLICA", "PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED"]
)
def test_query_deployment_gates_are_exact_and_read_per_request(query_api, monkeypatch, name):
    client, calls = query_api
    endpoint = "/api/v1/query/catalog?env=prod"
    for value in (None, "true", "TRUE", "yes", " 1 ", "0", "invalid"):
        if value is None:
            monkeypatch.delenv(name)
        else:
            monkeypatch.setenv(name, value)
        assert client.get(endpoint).status_code == 503
        assert calls == []
    monkeypatch.setenv(name, "1")
    response = client.get(endpoint)
    assert response.status_code == 200
    assert response.json()["catalogs"][0]["name"] == "warehouse_prod"
    monkeypatch.setenv(name, "0")
    assert client.get(endpoint).status_code == 503


@pytest.mark.parametrize("supported", [False, True])
def test_catalog_probes_only_role_tables_and_retains_supported_metadata(
    query_api, monkeypatch, supported
):
    client, calls = query_api
    tables = ["roles", "applicable_roles", "enabled_roles", "columns", "tables"]

    async def execute(sql, *, catalog, disconnected, limit):
        calls.append((sql, catalog, limit))
        if "SELECT table_schema" in sql:
            return {
                "rows": [{"table_schema": "information_schema", "table_name": t} for t in tables]
                + [{"table_schema": "raw", "table_name": "roles"}],
                "has_more": False,
            }
        if sql.endswith('."roles" LIMIT 1') and not supported:
            raise v1_query.PreviewQueryRejected("unsupported", error_name="NOT_SUPPORTED")
        if sql.endswith('."applicable_roles" LIMIT 1'):
            raise v1_query.PreviewQueryRejected("denied", error_name="PERMISSION_DENIED")
        if sql.endswith('."enabled_roles" LIMIT 1'):
            raise v1_query.PreviewUnavailable("outage")
        return {"rows": [], "has_more": False}

    monkeypatch.setattr(v1_query, "execute_preview", execute)
    response = client.get("/api/v1/query/catalog?env=staging")
    assert response.status_code == 200
    schemas = response.json()["catalogs"][0]["schemas"]
    assert schemas == [
        {"name": "information_schema", "tables": tables if supported else tables[1:]},
        {"name": "raw", "tables": ["roles"]},
    ]
    assert len(calls) == 4
    assert all(catalog == "warehouse_staging" for _, catalog, _ in calls)
    assert all(limit == 1 and "LIMIT 1" in sql for sql, _, limit in calls[1:])


def test_catalog_role_probe_timeout_cancels_pending_work_without_hiding_tables(monkeypatch):
    cancelled: list[str] = []

    async def execute(sql, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(sql)

    monkeypatch.setattr(v1_query, "execute_preview", execute)
    started = time.monotonic()
    assert (
        asyncio.run(
            v1_query._unsupported_role_tables("warehouse_prod", ["roles", "roles", "orders"])
        )
        == set()
    )
    assert time.monotonic() - started < 7
    assert len(cancelled) == 1


def test_query_result_is_bounded_owner_scoped_exportable_and_audited(query_api, tmp_path):
    client, calls = query_api
    response = client.post(
        "/api/v1/queries?env=prod",
        json={"sql": "SELECT id FROM analytics.orders", "row_limit": 1},
    )
    assert response.status_code == 202, response.text
    query_id = response.json()["id"]
    result = _wait_for_result(client, query_id)
    assert result["status"] == "completed"
    assert result["result"]["rows"] == [{"id": 7}]
    assert calls[-1][1:] == ("warehouse_prod", 1)
    assert calls[-1][0].endswith('AS "_phlo_query_result" LIMIT 2')
    csv = client.get(f"/api/v1/queries/{query_id}/csv?env=prod")
    assert csv.status_code == 200
    assert csv.text == "id\r\n7\r\n"
    audit_log = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    assert '"operation": "query.attempt"' in audit_log.read_text()


def test_catalog_outage_is_not_reported_as_an_empty_catalog(query_api, monkeypatch):
    client, _ = query_api

    async def unavailable(*_args, **_kwargs):
        raise v1_query.PreviewUnavailable("offline")

    monkeypatch.setattr(v1_query, "execute_preview", unavailable)
    response = client.get("/api/v1/query/catalog?env=prod")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"


def test_query_rejects_writes_and_staging_catalog_qualification(query_api, tmp_path):
    client, calls = query_api
    for sql in (
        "DELETE FROM analytics.orders",
        "SELECT * FROM warehouse_staging.analytics.orders",
        "SELECT 1; SELECT 2",
    ):
        response = client.post("/api/v1/queries?env=prod", json={"sql": sql})
        assert response.status_code == 422
    assert calls == []
    audit = (tmp_path / ".phlo" / "audit" / "operations.jsonl").read_text()
    assert audit.count('"operation": "query.attempt"') == 6


def test_query_cancel_stops_the_active_execution(query_api, monkeypatch):
    client, _ = query_api

    async def waiting_query(sql, *, catalog, disconnected, limit, on_progress, should_cancel):
        on_progress("trino-query-1", "https://trino.example/v1/next/query-1")
        await asyncio.Event().wait()

    monkeypatch.setattr(v1_query, "execute_preview", waiting_query)
    response = client.post("/api/v1/queries?env=prod", json={"sql": "SELECT 1"})
    query_id = response.json()["id"]
    for _ in range(20):
        if v1_query._QUERY_SESSIONS[query_id].active_uri is not None:
            break
        time.sleep(0.01)
    cancelled = client.post(f"/api/v1/queries/{query_id}/cancel?env=prod")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelling"
    assert _wait_for_result(client, query_id)["status"] == "cancelled"


def test_query_execution_outage_returns_a_terminal_unavailable_state(query_api, monkeypatch):
    client, _ = query_api

    async def unavailable(*_args, **_kwargs):
        raise v1_query.PreviewUnavailable("backend internals must not be exposed")

    monkeypatch.setattr(v1_query, "execute_preview", unavailable)
    submitted = client.post("/api/v1/queries?env=prod", json={"sql": "SELECT 1"})
    result = _wait_for_result(client, submitted.json()["id"])

    assert submitted.status_code == 202
    assert result["status"] == "failed"
    assert result["error"] == "Query engine is unavailable or rejected the query."


def test_query_policy_denials_are_audited_before_sql_reaches_trino(monkeypatch, tmp_path):
    actor = AuthPrincipal(subject="denied-analyst", principal_type="user", groups=())

    class DenyBackend:
        def explain_decision(self, principal, action, resource, context):
            assert action == "dataset.query"
            assert context.environment == "prod"
            return AuthorizationDecision(allowed=False, reason_code="explicit_deny")

    monkeypatch.setattr(security_manifest, "get_request_principal", lambda _request: actor)
    monkeypatch.setattr(
        security_manifest,
        "resolve_request_principal",
        lambda *_args, **_kwargs: Principal(subject="denied-analyst", principal_type="user"),
    )
    monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: DenyBackend())
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: False)
    monkeypatch.setattr(v1_query, "get_request_principal", lambda _request: actor)
    monkeypatch.setattr(
        v1_query,
        "execute_preview",
        lambda *_args, **_kwargs: pytest.fail("denied SQL must not reach Trino"),
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    with TestClient(full_app) as client:
        response = client.post(
            "/api/v1/queries?env=prod", json={"sql": "SELECT secret FROM finance.records"}
        )

    assert response.status_code == 403
    record = json.loads((tmp_path / ".phlo" / "audit" / "operations.jsonl").read_text())
    assert record["operation"] == "query.authorization_denied"
    assert record["subject"] == "denied-analyst"
    assert record["payload"]["sql_sha256"]
    assert "SELECT secret" not in json.dumps(record)


def test_explain_and_saved_query_crud_rejects_stale_versions(query_api):
    client, calls = query_api
    explained = client.post(
        "/api/v1/queries/explain?env=staging", json={"sql": "SELECT id FROM analytics.orders"}
    )
    assert explained.status_code == 202
    assert _wait_for_result(client, explained.json()["id"], "staging")["status"] == "completed"
    assert calls[-1][1] == "warehouse_staging"

    created = client.post(
        "/api/v1/queries/saved?env=prod",
        headers={"Idempotency-Key": "create-1"},
        json={"env": "prod", "name": "Orders", "sql": "SELECT id FROM analytics.orders"},
    )
    assert created.status_code == 201, created.text
    query = created.json()
    replay = client.post(
        "/api/v1/queries/saved?env=prod",
        headers={"Idempotency-Key": "create-1"},
        json={"env": "prod", "name": "Orders", "sql": "SELECT id FROM analytics.orders"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == query["id"]
    assert client.get("/api/v1/queries/saved?env=prod").json()["items"][0]["id"] == query["id"]
    assert client.get("/api/v1/queries/saved?env=staging").json()["items"] == []

    update = {
        "env": "prod",
        "expected_version": 1,
        "name": "Orders v2",
        "sql": "SELECT id FROM analytics.orders",
    }
    updated = client.put(
        f"/api/v1/queries/saved/{query['id']}?env=prod",
        headers={"Idempotency-Key": "update-1"},
        json=update,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    stale = client.put(
        f"/api/v1/queries/saved/{query['id']}?env=prod",
        headers={"Idempotency-Key": "update-stale"},
        json=update,
    )
    assert stale.status_code == 409
    deleted = client.request(
        "DELETE",
        f"/api/v1/queries/saved/{query['id']}?env=prod",
        headers={"Idempotency-Key": "delete-1"},
        json={"env": "prod", "expected_version": 2},
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/queries/saved?env=prod").json()["items"] == []
