"""HTTP and event-stream contracts for the first environment-scoped API slice."""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api.api import v1
from phlo_api.api import v1_assets
from phlo_api.main import app
from phlo_api import security_manifest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "production_jobs", "nessie_ref": "main"},
                "staging": {"dagster_location": "testing_jobs", "nessie_ref": "candidate"},
            }
        ),
    )
    monkeypatch.setattr(v1, "project_env_value", lambda name: "http://nessie:19120/api/v2")
    monkeypatch.setattr(
        v1.ServiceDiscovery, "discover", lambda self: {"trino": object(), "dagster": object()}
    )
    auth = AuthPrincipal(
        subject="alice", principal_type="user", email="alice@example.org", groups=("operator",)
    )
    monkeypatch.setattr(security_manifest, "get_request_principal", lambda request: auth)
    monkeypatch.setattr(v1, "get_request_principal", lambda request: auth)
    from phlo_api import incidents

    monkeypatch.setattr(incidents, "get_request_principal", lambda request: auth)
    monkeypatch.setattr(
        security_manifest,
        "resolve_request_principal",
        lambda *args, **kwargs: Principal(
            subject="alice", principal_type="user", roles=("operator",)
        ),
    )
    monkeypatch.setattr(
        v1,
        "resolve_request_principal",
        lambda *args, **kwargs: Principal(
            subject="alice", principal_type="user", roles=("operator",)
        ),
    )
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: False)
    monkeypatch.setattr(v1, "is_regulated", lambda: False)
    calls = []

    class Backend:
        def explain_decision(self, principal, action, resource, context):
            calls.append((action, resource.resource_id, context.environment))
            return AuthorizationDecision(allowed=True, reason_code="explicit_allow")

    backend_provider = Backend()
    monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: backend_provider)
    monkeypatch.setattr(v1, "get_authorization_backend", lambda: backend_provider)

    async def graphql(url, query, *args, **kwargs):
        if "Locations" in query:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [
                            {"location": {"name": "production_jobs"}},
                            {"location": {"name": "testing_jobs"}},
                        ],
                    }
                }
            }
        return {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [
                        {
                            "runId": "p-run",
                            "status": "STARTED",
                            "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                        },
                        {
                            "runId": "s-run",
                            "status": "FAILURE",
                            "repositoryOrigin": {"repositoryLocationName": "testing_jobs"},
                        },
                    ],
                }
            }
        }

    monkeypatch.setattr(v1, "graphql_request", graphql)
    urls = []

    class HTTP:
        async def get(self, url, **kwargs):
            urls.append(url)
            return httpx.Response(
                200,
                json={"reference": {"type": "BRANCH", "name": url.rsplit("/", 1)[-1]}},
                request=httpx.Request("GET", url),
            )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def backend():
        yield HTTP()

    monkeypatch.setattr(v1, "backend_client", backend)
    with TestClient(app) as test_client:
        yield test_client, calls, urls, monkeypatch, backend_provider


def test_identity_and_environment_scoped_services(client):
    http, decisions, urls, _, _ = client
    identity = http.get("/api/v1/me")
    assert identity.status_code == 200
    assert identity.json() == {
        "subject": "alice",
        "principal_type": "user",
        "email": "alice@example.org",
        "roles": ["operator"],
        "permissions": {
            "prod": ["service.read", "run.read"],
            "staging": ["service.read", "run.read"],
        },
    }
    environments = http.get("/api/v1/environments")
    assert environments.json() == {
        "items": [{"env": "prod", "status": "available"}, {"env": "staging", "status": "available"}]
    }
    for env, ref in (("prod", "main"), ("staging", "candidate")):
        response = http.get(f"/api/v1/services?env={env}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["env"] == env and data["next_cursor"] is None
        services = {item["id"]: item for item in data["items"]}
        assert services["dagster"]["status"] == "healthy"
        assert services["dagster"]["response_time_seconds"] >= 0
        assert services["nessie"]["status"] == "healthy"
        assert services["trino"] == {
            "id": "trino",
            "status": "unknown",
            "observed_at": None,
            "response_time_seconds": None,
        }
        assert urls[-1] == f"http://nessie:19120/api/v2/trees/{ref}"
        assert ("service.read", f"env={env}", env) in decisions


def test_incident_routes_are_enforced_even_when_legacy_authorization_is_optional(client):
    http, decisions, _, _, backend = client
    backend.explain_decision = lambda principal, action, resource, context: AuthorizationDecision(
        allowed=False, reason_code="explicit_deny"
    )
    denied = http.get("/api/v1/incidents?env=prod")
    assert denied.status_code == 403
    assert http.get("/api/v1/incident-policies?env=prod").status_code == 403

    backend.explain_decision = lambda principal, action, resource, context: (
        decisions.append((action, resource.resource_id, context.environment))
        or AuthorizationDecision(allowed=True, reason_code="explicit_allow")
    )
    unavailable = http.get("/api/v1/assets/orders/incident-policy?env=staging")
    assert unavailable.status_code == 503
    assert ("asset.read", "env=staging|asset_id=orders", "staging") in decisions
    unavailable_policies = http.get("/api/v1/incident-policies?env=prod")
    assert unavailable_policies.status_code == 503
    assert ("asset.read", "env=prod", "prod") in decisions


def test_incident_signal_write_requires_asset_policy_for_selected_environment(client):
    http, decisions, _, _, backend = client
    payload = {
        "asset_id": "warehouse.orders",
        "kind": "failed_check",
        "title": "Orders check failed",
        "evidence": {"check_name": "orders.pk", "dagster_run_id": "run-1"},
        "evidence_id": "dagster-check:prod-location:73",
    }
    backend.explain_decision = lambda principal, action, resource, context: AuthorizationDecision(
        allowed=False, reason_code="explicit_deny"
    )
    denied = http.post(
        "/api/v1/incidents?env=prod",
        json=payload,
        headers={"Idempotency-Key": payload["evidence_id"]},
    )
    assert denied.status_code == 403

    backend.explain_decision = lambda principal, action, resource, context: (
        decisions.append((action, resource.resource_id, context.environment))
        or AuthorizationDecision(allowed=True, reason_code="explicit_allow")
    )
    unavailable = http.post(
        "/api/v1/incidents?env=staging",
        json=payload,
        headers={"Idempotency-Key": payload["evidence_id"]},
    )
    assert unavailable.status_code == 503
    assert ("asset.manage", "env=staging|asset_id=warehouse.orders", "staging") in decisions


def test_invalid_selection_missing_identity_policy_and_mapping_fail_closed(client):
    http, decisions, _, monkeypatch, backend_provider = client
    for path in ("/api/v1/services", "/api/v1/services?env=other", "/api/v1/events?env=other"):
        response = http.get(path)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable_input"
    repeated = http.get("/api/v1/assets?env=prod&env=staging")
    assert repeated.status_code == 422
    assert http.get("/api/v1/services?env=prod&nessie_ref=candidate").status_code == 400
    monkeypatch.setattr(security_manifest, "get_request_principal", lambda request: None)
    assert http.get("/api/v1/me").status_code == 401
    monkeypatch.setattr(
        security_manifest,
        "get_request_principal",
        lambda request: AuthPrincipal(subject="alice", principal_type="user"),
    )
    monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: None)
    assert http.get("/api/v1/services?env=prod").status_code == 503
    assert ("service.read", "env=prod", "prod") in decisions
    monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: backend_provider)
    monkeypatch.delenv("PHLO_V1_ENVIRONMENTS")
    assert http.get("/api/v1/environments").status_code == 503


def test_environment_allowlist_requires_distinct_targets(client):
    http, _, _, monkeypatch, _ = client
    for staging in (
        {"dagster_location": "production_jobs", "nessie_ref": "candidate"},
        {"dagster_location": "testing_jobs", "nessie_ref": "main"},
        {"dagster_location": "testing_jobs", "nessie_ref": "../main"},
    ):
        monkeypatch.setenv(
            "PHLO_V1_ENVIRONMENTS",
            json.dumps(
                {
                    "prod": {"dagster_location": "production_jobs", "nessie_ref": "main"},
                    "staging": staging,
                }
            ),
        )
        assert http.get("/api/v1/services?env=staging").status_code == 503


def test_run_report_scoped_service_token_cannot_read_v1(client):
    http, _, _, monkeypatch, _ = client
    scoped = AuthPrincipal(
        subject="run-reporter",
        principal_type="service",
        attributes={security_manifest.RUN_REPORT_RESOURCE_ID_ATTRIBUTE: "project_id=x|run_id=y"},
    )
    monkeypatch.setattr(security_manifest, "get_request_principal", lambda request: scoped)
    for path in (
        "/api/v1/me",
        "/api/v1/environments",
        "/api/v1/services?env=prod",
        "/api/v1/events?env=prod",
    ):
        assert http.get(path).status_code == 403


def test_me_uses_regulated_canonical_roles(client):
    http, _, _, monkeypatch, _ = client
    from phlo.security.adapters import EnforcementResult

    class Context:
        def canonicalize(self, principal):
            assert principal.subject == "alice"
            return Principal(subject="alice", principal_type="user", roles=("auditor",))

    monkeypatch.setattr(v1, "is_regulated", lambda: True)
    monkeypatch.setattr(v1.EnforcementContext, "get_instance", lambda: Context())
    monkeypatch.setattr(v1, "enforce", lambda **kwargs: EnforcementResult.allow())
    response = http.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["roles"] == ["auditor"]


def test_missing_sources_do_not_report_healthy_or_empty(client):
    http, _, _, monkeypatch, _ = client

    async def down(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(v1, "graphql_request", down)
    monkeypatch.setattr(v1, "project_env_value", lambda name: None)
    response = http.get("/api/v1/environments")
    assert response.json() == {
        "items": [
            {"env": "prod", "status": "unavailable"},
            {"env": "staging", "status": "unavailable"},
        ]
    }
    for env in ("prod", "staging"):
        response = http.get(f"/api/v1/services?env={env}")
        assert {item["id"]: item["status"] for item in response.json()["items"]}[
            "dagster"
        ] == "unavailable"
        assert {item["id"]: item["status"] for item in response.json()["items"]}[
            "nessie"
        ] == "unavailable"
        assert http.get(f"/api/v1/events?env={env}").status_code == 503
    monkeypatch.setattr(v1.ServiceDiscovery, "discover", lambda self: {})
    assert http.get("/api/v1/services?env=prod").status_code == 503


def test_missing_staging_location_does_not_fall_back_to_prod(client):
    http, _, _, monkeypatch, _ = client
    monkeypatch.setattr(v1, "_locations", lambda: asyncio.sleep(0, result={"production_jobs"}))
    response = http.get("/api/v1/environments")
    assert response.json()["items"] == [
        {"env": "prod", "status": "available"},
        {"env": "staging", "status": "unavailable"},
    ]
    staging = http.get("/api/v1/services?env=staging")
    assert {item["id"]: item["status"] for item in staging.json()["items"]}[
        "dagster"
    ] == "unhealthy"
    assert http.get("/api/v1/events?env=staging").status_code == 503


def test_policy_filters_environments_and_denies_cross_environment_read(client):
    http, _, urls, monkeypatch, _ = client

    class ProdOnly:
        def explain_decision(self, principal, action, resource, context):
            allowed = context.environment != "staging"
            return AuthorizationDecision(
                allowed=allowed, reason_code="explicit_allow" if allowed else "explicit_deny"
            )

    monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: ProdOnly())
    monkeypatch.setattr(v1, "get_authorization_backend", lambda: ProdOnly())
    assert http.get("/api/v1/me").json()["permissions"]["staging"] == []
    assert http.get("/api/v1/environments").json()["items"] == [
        {"env": "prod", "status": "available"}
    ]
    before = len(urls)
    assert http.get("/api/v1/services?env=staging").status_code == 403
    assert http.get("/api/v1/events?env=staging").status_code == 403
    for path in ("services", "events"):
        for selection in ("prod&env=staging", "staging&env=prod"):
            assert http.get(f"/api/v1/{path}?env={selection}").status_code == 422
    assert len(urls) == before


def test_run_location_validation_never_returns_an_empty_success(client):
    http, _, _, monkeypatch, _ = client

    async def missing_origin(*args, **kwargs):
        if "Locations" in args[1]:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [{"location": {"name": "production_jobs"}}],
                    }
                }
            }
        return {
            "data": {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [{"runId": "orphan", "status": "SUCCESS", "repositoryOrigin": None}],
                }
            }
        }

    monkeypatch.setattr(v1, "graphql_request", missing_origin)
    assert http.get("/api/v1/events?env=prod").status_code == 502


def test_run_source_filters_asymmetric_locations(client):
    assert asyncio.run(v1._runs("production_jobs")) == {"p-run": "STARTED"}
    assert asyncio.run(v1._runs("testing_jobs")) == {"s-run": "FAILURE"}


def test_events_openapi_advertises_sse():
    content = app.openapi()["paths"]["/api/v1/events"]["get"]["responses"]["200"]["content"]
    assert set(content) == {"text/event-stream"}


def test_nessie_wrong_ref_is_not_healthy(client):
    http, _, _, monkeypatch, _ = client
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def wrong_ref():
        class HTTP:
            async def get(self, url, **kwargs):
                return httpx.Response(
                    200,
                    json={"reference": {"type": "BRANCH", "name": "main"}},
                    request=httpx.Request("GET", url),
                )

        yield HTTP()

    monkeypatch.setattr(v1, "backend_client", wrong_ref)
    response = http.get("/api/v1/services?env=staging")
    assert {item["id"]: item["status"] for item in response.json()["items"]}[
        "nessie"
    ] == "unavailable"
    assert http.get("/api/v1/events?env=staging").status_code == 503


def test_events_emit_real_scoped_changes_and_reject_resume(client):
    http, decisions, _, monkeypatch, _ = client
    snapshots = itertools.count()

    async def service(target):
        n = next(snapshots)
        return [
            v1.ServiceSnapshot(
                id="trino",
                status="healthy" if n == 0 else "unhealthy",
                observed_at=v1.datetime.now(v1.timezone.utc),
                response_time_seconds=0.1,
            )
        ]

    runs_seen = itertools.count()

    async def runs(location):
        n = next(runs_seen)
        return {
            "p-run" if location == "production_jobs" else "s-run": "STARTED"
            if n == 0
            else "SUCCESS"
        }

    original_sleep = asyncio.sleep

    async def immediate(_):
        await original_sleep(0)

    monkeypatch.setattr(v1, "_service_snapshots", service)
    monkeypatch.setattr(v1, "_runs", runs)
    monkeypatch.setattr(v1, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(v1.asyncio, "sleep", immediate)
    ticks = iter((0, 1, 61))
    response = http.get("/api/v1/events?env=prod")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = response.text.split("\n\n")
    assert "event: service.status" in frames[0]
    assert '"env":"prod"' in frames[0]
    assert "event: run.status" in frames[1]
    assert '"run_id":"p-run"' in frames[1] and '"status":"SUCCESS"' in frames[1]
    assert "s-run" not in response.text
    assert frames[0].startswith("id: ") and frames[1].startswith("id: ")
    assert response.text.endswith(": heartbeat\n\n")
    assert ("run.read", "env=prod", "prod") in decisions
    assert ("service.read", "env=prod", "prod") in decisions
    rejected = http.get("/api/v1/events?env=staging", headers={"Last-Event-ID": "old:1"})
    assert rejected.status_code == 400 and rejected.json()["error"]["code"] == "resync_required"


def test_events_emit_service_down_before_error(client):
    http, _, _, monkeypatch, _ = client
    original_sleep = asyncio.sleep
    for down_status in ("unhealthy", "unavailable"):
        seen = itertools.count()

        async def service(target):
            status = "healthy" if next(seen) == 0 else down_status
            return [
                v1.ServiceSnapshot(
                    id="nessie",
                    status=status,
                    observed_at=v1.datetime.now(v1.timezone.utc)
                    if status != "unavailable"
                    else None,
                    response_time_seconds=0.2 if status != "unavailable" else None,
                )
            ]

        async def runs(location):
            return {"p-run": "STARTED"}

        async def immediate(_):
            await original_sleep(0)

        ticks = iter((0, 1, 61))
        monkeypatch.setattr(v1, "_service_snapshots", service)
        monkeypatch.setattr(v1, "_runs", runs)
        monkeypatch.setattr(v1, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(v1.asyncio, "sleep", immediate)
        response = http.get("/api/v1/events?env=prod")
        assert response.status_code == 200
        assert "event: service.status" in response.text
        assert f'"status":"{down_status}"' in response.text
        assert response.text.index("event: service.status") < response.text.index("event: error")
        assert '"code":"backend_unavailable"' in response.text


def test_assets_are_location_scoped_paginated_and_authorized(client, monkeypatch):
    http, decisions, _, _, backend = client
    nodes = [
        {
            "id": "p-orders",
            "assetKey": {"path": ["orders"]},
            "description": "prod orders",
            "computeKind": "dbt",
            "groupName": "warehouse",
            "isSource": False,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [{"timestamp": "1780000000", "runId": "p-run"}],
        },
        {
            "id": "s-orders",
            "assetKey": {"path": ["staging_orders"]},
            "description": "staging orders",
            "computeKind": "dbt",
            "groupName": "warehouse",
            "isSource": True,
            "repository": {"name": "repo", "location": {"name": "testing_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [{"timestamp": "1781000000", "runId": "s-run"}],
        },
    ]

    async def graphql(url, query, *args, **kwargs):
        return {"data": {"assetNodes": nodes}}

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    prod = http.get("/api/v1/assets?env=prod&limit=1")
    staging = http.get("/api/v1/assets?env=staging")
    assert prod.status_code == staging.status_code == 200
    assert [item["description"] for item in prod.json()["items"]] == ["prod orders"]
    assert prod.json()["items"][0]["last_run_id"] == "p-run"
    assert [item["description"] for item in staging.json()["items"]] == ["staging orders"]
    assert prod.json()["next_cursor"] is None
    assert ("asset.read", "env=prod", "prod") in decisions
    assert ("asset.read", "env=staging", "staging") in decisions

    backend.explain_decision = lambda *args: AuthorizationDecision(
        allowed=False, reason_code="explicit_deny"
    )
    denied = http.get("/api/v1/assets?env=prod")
    assert denied.status_code == 403


def test_asset_cursor_is_environment_bound_and_sources_filter_before_page(client, monkeypatch):
    http, _, _, _, _ = client
    nodes = [
        {
            "id": key,
            "assetKey": {"path": [key]},
            "description": key,
            "computeKind": None,
            "groupName": None,
            "isSource": source,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [],
        }
        for key, source in (("a", False), ("b", True), ("c", True))
    ]

    async def graphql(url, query, *args, **kwargs):
        return {"data": {"assetNodes": nodes}}

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    first = http.get("/api/v1/sources?env=prod&limit=1")
    assert [item["id"] for item in first.json()["items"]] == ["b"]
    cursor = first.json()["next_cursor"]
    second = http.get(f"/api/v1/sources?env=prod&limit=1&cursor={cursor}")
    assert [item["id"] for item in second.json()["items"]] == ["c"]
    wrong_collection = http.get(f"/api/v1/assets?env=prod&cursor={cursor}")
    assert wrong_collection.status_code == 400
    crossed = http.get(f"/api/v1/assets?env=staging&cursor={cursor}")
    assert crossed.status_code == 400


def test_shared_asset_key_does_not_expose_unscoped_history(client, monkeypatch):
    http, _, _, _, _ = client
    nodes = [
        {
            "id": env,
            "assetKey": {"path": ["orders"]},
            "description": env,
            "computeKind": None,
            "groupName": None,
            "isSource": False,
            "repository": {"name": "repo", "location": {"name": location}},
            "dependencyKeys": [],
            "assetMaterializations": [{"timestamp": "1780000000", "runId": env}],
        }
        for env, location in (("prod", "production_jobs"), ("staging", "testing_jobs"))
    ]

    async def graphql(url, query, *args, **kwargs):
        return {"data": {"assetNodes": nodes}}

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    response = http.get("/api/v1/assets?env=prod")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["history_scoped"] is False
    assert item["last_materialization_at"] is None
    assert item["last_run_id"] is None
    assert http.get("/api/v1/assets/orders/runs?env=prod").status_code == 503
    assert http.get("/api/v1/assets/orders?env=prod").status_code == 503


def test_asset_detail_exposes_typed_columns_and_environment_bound_history(client, monkeypatch):
    http, _, _, _, _ = client
    node = {
        "id": "p-orders",
        "assetKey": {"path": ["orders"]},
        "description": "orders",
        "computeKind": "dbt",
        "groupName": "warehouse",
        "isSource": False,
        "repository": {"name": "repo", "location": {"name": "production_jobs"}},
        "dependencyKeys": [],
        "assetMaterializations": [{"timestamp": "1780000000", "runId": "p-run"}],
        "metadataEntries": [],
    }
    detail = {
        **node,
        "assetMaterializations": [
            {
                "timestamp": "1780000000",
                "runId": "p-run",
                "metadataEntries": [
                    {
                        "schema": {
                            "columns": [{"name": "id", "type": "BIGINT", "description": None}]
                        }
                    },
                    {
                        "lineage": [
                            {
                                "columnName": "id",
                                "columnDeps": [
                                    {"assetKey": {"path": ["raw", "orders"]}, "columnName": "id"}
                                ],
                            }
                        ]
                    },
                ],
            }
        ],
    }

    async def graphql(url, query, variables=None):
        if "V1AssetDetail" in query:
            return {"data": {"assetNodeOrError": {"__typename": "AssetNode", **detail}}}
        return {"data": {"assetNodes": [node]}}

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    response = http.get("/api/v1/assets/orders?env=prod")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["columns"] == [{"name": "id", "type": "BIGINT", "description": None}]
    assert payload["schema_observed_at"] is not None
    assert payload["column_lineage"] == {
        "id": [{"asset_key": ["raw", "orders"], "column_name": "id"}]
    }


def test_asset_routes_report_dagster_outage_without_empty_success(client, monkeypatch):
    http, _, _, _, _ = client

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(v1_assets, "graphql_request", unavailable)
    response = http.get("/api/v1/assets?env=prod")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"


def test_iceberg_history_is_bound_to_configured_environment_ref(client, monkeypatch):
    http, _, _, _, _ = client
    refs = []

    def history(table_name, ref, limit):
        refs.append((table_name, ref, limit))
        return [
            {
                "snapshot_id": 91 if ref == "main" else 17,
                "timestamp_ms": 1780000000000,
                "operation": "append",
                "summary": {"added-records": "3"},
                "parent_id": None,
            }
        ]

    monkeypatch.setattr(v1_assets, "_iceberg_history", history)
    prod = http.get("/api/v1/tables/warehouse.orders/snapshots?env=prod&limit=5")
    staging = http.get("/api/v1/tables/warehouse.orders/snapshots?env=staging&limit=5")
    assert prod.status_code == staging.status_code == 200
    assert prod.json()["nessie_ref"] == "main"
    assert staging.json()["nessie_ref"] == "candidate"
    assert prod.json()["items"][0]["snapshot_id"] == 91
    assert staging.json()["items"][0]["snapshot_id"] == 17
    assert refs == [
        ("warehouse.orders", "main", 5),
        ("warehouse.orders", "candidate", 5),
    ]


def test_iceberg_history_rejects_invalid_table_and_propagates_unavailable(client, monkeypatch):
    http, _, _, _, _ = client
    assert http.get("/api/v1/tables/warehouse.orders;drop/snapshots?env=prod").status_code == 400

    def unavailable(*args):
        raise OSError("catalog unavailable")

    monkeypatch.setattr(v1_assets, "_iceberg_history", unavailable)
    response = http.get("/api/v1/tables/warehouse.orders/snapshots?env=prod")
    assert response.status_code == 503


def test_schema_history_uses_environment_ref_and_limits_versions(client, monkeypatch):
    http, _, _, _, _ = client
    refs = []

    def schema(table_name, ref):
        refs.append((table_name, ref))
        return 3, [{"schema_id": index, "fields": []} for index in range(4)]

    monkeypatch.setattr(v1_assets, "_iceberg_schema", schema)
    response = http.get("/api/v1/tables/warehouse.orders/schema-history?env=staging&limit=2")
    assert response.status_code == 200, response.text
    assert response.json()["nessie_ref"] == "candidate"
    assert response.json()["current_schema_id"] == 3
    assert [version["schema_id"] for version in response.json()["items"]] == [2, 3]
    assert refs == [("warehouse.orders", "candidate")]


def test_materialization_estimate_reports_cost_unavailable_not_fabricated(client, monkeypatch):
    http, _, _, _, _ = client
    monkeypatch.setattr(
        v1_assets,
        "_assets",
        lambda *args, **kwargs: asyncio.sleep(
            0,
            result=[
                v1_assets.AssetView(
                    id="warehouse/orders",
                    key=["warehouse", "orders"],
                    description=None,
                    compute_kind=None,
                    group_name=None,
                    is_source=False,
                    dependencies=[],
                    last_materialization_at=None,
                    last_run_id=None,
                )
            ],
        ),
    )
    response = http.get(
        "/api/v1/assets/warehouse/orders/materialization-estimate?env=prod&partition_count=3"
    )
    assert response.status_code == 200, response.text
    assert response.json()["partition_count"] == 3
    assert response.json()["estimated_cost"] is None
    assert response.json()["estimated_bytes"] is None
    assert response.json()["estimated_duration_seconds"] is None
    assert "no cost source" in response.json()["cost_status"]
    assert "no workload source" in response.json()["workload_status"]


def test_asset_preview_uses_exact_environment_catalog_and_ref(client, monkeypatch):
    http, *_ = client
    calls = []

    async def graphql(query, variables=None):
        return {
            "data": {
                "assetNodes": [
                    {
                        "id": "orders-id",
                        "assetKey": {"path": ["warehouse", "orders"]},
                        "description": None,
                        "computeKind": "python",
                        "groupName": "warehouse",
                        "isSource": False,
                        "repository": {
                            "name": "repo",
                            "location": {"name": "production_jobs"},
                        },
                        "dependencyKeys": [],
                        "assetMaterializations": [],
                    }
                ]
            }
        }

    async def preview(sql, *, catalog, disconnected, limit):
        calls.append((sql, catalog, limit))
        return {
            "columns": [{"name": "id", "type": "bigint"}],
            "rows": [{"id": 7}],
            "has_more": False,
        }

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    monkeypatch.setattr(v1_assets, "execute_preview", preview)
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_USER", "phlo_preview")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "iceberg_prod", "nessie_ref": "main"},
                "staging": {"catalog": "iceberg_stage", "nessie_ref": "candidate"},
            }
        ),
    )
    prod = http.get("/api/v1/assets/warehouse/orders/preview?env=prod&limit=1")
    assert prod.status_code == 200, prod.text
    assert prod.json()["nessie_ref"] == "main"
    assert calls[0][1] == "iceberg_prod"
    assert calls[0][0] == 'SELECT * FROM "iceberg_prod"."warehouse"."orders" LIMIT 2'


def test_asset_preview_refuses_same_key_from_multiple_locations(client, monkeypatch):
    http, *_ = client

    async def graphql(query, variables=None):
        return {
            "data": {
                "assetNodes": [
                    {
                        "id": f"{location}-orders",
                        "assetKey": {"path": ["warehouse", "orders"]},
                        "description": None,
                        "computeKind": "python",
                        "groupName": "warehouse",
                        "isSource": False,
                        "repository": {"name": "repo", "location": {"name": location}},
                        "dependencyKeys": [],
                        "assetMaterializations": [],
                    }
                    for location in ("production_jobs", "testing_jobs")
                ]
            }
        }

    async def unexpected_preview(*args, **kwargs):
        raise AssertionError("ambiguous cross-location asset must not reach Trino")

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    monkeypatch.setattr(v1_assets, "execute_preview", unexpected_preview)
    response = http.get("/api/v1/assets/warehouse/orders/preview?env=prod")

    assert response.status_code == 503


def test_materialize_action_pins_location_ref_and_replay_key(client, monkeypatch, tmp_path):
    http, *_ = client
    from phlo_api.api import operation_controls
    from phlo_api.observatory_api import orchestrator_operations
    from phlo_api.observatory_api import run_action_contract

    node = {
        "id": "orders-id",
        "assetKey": {"path": ["warehouse", "orders"]},
        "description": None,
        "computeKind": "python",
        "groupName": "warehouse",
        "isSource": False,
        "repository": {"name": "prod_repo", "location": {"name": "production_jobs"}},
        "dependencyKeys": [],
        "assetMaterializations": [],
    }

    async def graphql(query, variables=None):
        if "V1Assets" in query:
            return {"data": {"assetNodes": [node]}}
        return {"data": {"assetNodeOrError": {"__typename": "AssetNode", **node}}}

    calls = []

    class Provider:
        async def materialize_asset(self, asset_id, request):
            calls.append((asset_id, request))
            return {"accepted": True, "dry_run": request["dry_run"]}

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda *_: {"subject": "alice", "scopes": ["lakehouse:operate"]},
    )
    monkeypatch.setattr(operation_controls, "enforce_rate_limit", lambda *_: None)
    monkeypatch.setattr(run_action_contract, "require_idempotency_key", lambda key: key)
    monkeypatch.setattr(
        orchestrator_operations, "resolve_orchestrator_operations", lambda: Provider()
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))

    response = http.post(
        "/api/v1/assets/warehouse/orders/materialize?env=prod",
        json={"job_name": "warehouse_job", "idempotency_key": "req-1"},
    )
    replayed = http.post(
        "/api/v1/assets/warehouse/orders/materialize?env=prod",
        json={"job_name": "warehouse_job", "idempotency_key": "req-1"},
    )

    assert response.status_code == 200, response.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == response.json()
    assert len(calls) == 1
    assert response.json()["nessie_ref"] == "main"
    asset_id, action = calls[0]
    assert asset_id == "warehouse/orders"
    assert action["repository_location_name"] == "production_jobs"
    assert action["repository_name"] == "prod_repo"
    assert action["tags"] == {"environment": "prod", "phlo/ref": "main"}
    assert action["idempotency_key"] == "req-1"
    audit_log = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    assert len(audit_log.read_text().splitlines()) == 1


def test_backfill_action_requires_bounded_explicit_partitions(client, monkeypatch):
    http, *_ = client

    async def unexpected_graphql(*args, **kwargs):
        raise AssertionError("the single-replica gate must fail before provider discovery")

    monkeypatch.setattr(v1_assets, "_graphql", unexpected_graphql)
    disabled = http.post(
        "/api/v1/assets/warehouse/orders/backfill?env=prod",
        json={
            "partition_set_name": "daily",
            "partitions": ["2026-09-25"],
            "idempotency_key": "req-2",
        },
    )
    assert disabled.status_code == 503


def test_asset_check_history_filters_duplicate_key_runs_by_location(client, monkeypatch):
    http, _, _, _, _ = client
    nodes = [
        {
            "assetKey": {"path": ["warehouse", "orders"]},
            "repository": {"location": {"name": location}},
            "assetChecksOrError": {
                "__typename": "AssetChecks",
                "checks": [{"name": f"quality_{env}", "description": env}],
            },
        }
        for env, location in (("prod", "production_jobs"), ("staging", "testing_jobs"))
    ]
    executions: list[dict[str, Any]] = [
        {
            "status": "SUCCEEDED",
            "runId": run_id,
            "timestamp": 1780000000,
            "checkName": f"quality_{env}",
            "evaluation": {
                "severity": "ERROR",
                "metadataEntries": [
                    {"__typename": "IntMetadataEntry", "label": "rows", "intValue": count}
                ],
            },
        }
        for env, run_id, count in (("prod", "p-run", 9), ("staging", "s-run", 2))
    ]
    executions.append(
        {
            "status": "SUCCEEDED",
            "runId": None,
            "timestamp": 1780000000,
            "checkName": "runless-ambiguous",
            "evaluation": {"severity": "ERROR", "metadataEntries": []},
        }
    )
    locations = {"p-run": "production_jobs", "s-run": "testing_jobs"}

    async def graphql(url, query, variables=None):
        if "V1AssetChecks" in query:
            return {"data": {"assetNodes": nodes}}
        if "V1AssetCheckExecutions" in query:
            return {"data": {"assetCheckExecutions": executions}}
        if "V1AssetCheckRunLocation" in query:
            run_id = variables["runId"]
            return {
                "data": {
                    "runOrError": {
                        "__typename": "Run",
                        "runId": run_id,
                        "repositoryOrigin": {"repositoryLocationName": locations[run_id]},
                    }
                }
            }
        raise AssertionError("unexpected Dagster query")

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    prod = http.get("/api/v1/assets/warehouse/orders/checks?env=prod")
    staging = http.get("/api/v1/assets/warehouse/orders/checks?env=staging")
    assert prod.status_code == staging.status_code == 200
    assert prod.json()["definitions"] == [{"name": "quality_prod", "description": "prod"}]
    assert staging.json()["definitions"] == [{"name": "quality_staging", "description": "staging"}]
    assert [item["run_id"] for item in prod.json()["executions"]] == ["p-run"]
    assert [item["run_id"] for item in staging.json()["executions"]] == ["s-run"]
    assert all(
        item["check_name"] != "runless-ambiguous"
        for response in (prod, staging)
        for item in response.json()["executions"]
    )
    assert prod.json()["executions"][0]["metadata"] == [{"label": "rows", "value": 9}]


def test_overview_uses_incident_and_explicit_sla_evidence(client, monkeypatch):
    from types import SimpleNamespace

    http, _, _, _, _ = client
    nodes = [
        {
            "id": key,
            "assetKey": {"path": [key]},
            "description": key,
            "computeKind": None,
            "groupName": "warehouse",
            "isSource": False,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [{"timestamp": "1780000000", "runId": "run-1"}]
            if key == "orders"
            else [],
        }
        for key in ("orders", "unmaterialized")
    ]

    async def graphql(url, query, *args, **kwargs):
        return {"data": {"assetNodes": nodes}}

    from phlo_api import incidents

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(
        incidents, "incident_stats", lambda request, env: {"env": env, "counts": {"open": 2}}
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda request, env, limit, cursor: SimpleNamespace(
            items=[{"asset_id": "orders", "freshness_sla_seconds": 60}], next_cursor=None
        ),
    )
    response = http.get("/api/v1/overview?env=prod")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset_count"] == 2
    assert body["materialized_asset_count"] == 1
    assert body["incident_counts"] == {"open": 2}
    assert body["freshness_counts"] == {"fresh": 0, "stale": 1, "unknown": 1}
    assert body["run_status_counts"] == {"STARTED": 1}
    assert body["run_history_truncated"] is False
    assert body["audit_counts"] is None
