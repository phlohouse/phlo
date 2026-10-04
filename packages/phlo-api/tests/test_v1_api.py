"""HTTP and event-stream contracts for the first environment-scoped API slice."""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api.api import v1
from phlo_api.api import v1_assets
from phlo_api.api.v1_audit_proposals import AssetAuditProposalRequest, generate_check_file
from phlo_api.api.v1_git_review import AssetAuditDraftPullRequest
from phlo_api.incidents import IncidentStatsResponse
from phlo_api.main import app
from phlo_api import security_manifest


def _asset_inventory_response(
    nodes: list[dict[str, Any]], *, repository_selector: dict[str, str] | None = None
) -> dict[str, Any]:
    repositories: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in nodes:
        repository = node["repository"]
        key = (repository["name"], repository["location"]["name"])
        repositories.setdefault(key, []).append(node)
    for location in ("production_jobs", "testing_jobs"):
        if not any(key[1] == location for key in repositories):
            repositories[("repo", location)] = []
    return {
        "data": {
            "repositoriesOrError": {
                "__typename": "RepositoryConnection",
                "nodes": [
                    {
                        "name": name,
                        "location": {"name": location},
                        "assetNodes": assets,
                    }
                    for (name, location), assets in repositories.items()
                    if repository_selector is None
                    or (name, location)
                    == (
                        repository_selector["repositoryName"],
                        repository_selector["repositoryLocationName"],
                    )
                ],
            }
        }
    }


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
    monkeypatch.setattr(v1_assets, "get_request_principal", lambda request: auth)
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
                            "tags": [{"key": "phlo/ref", "value": "main"}],
                            "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                        },
                        {
                            "runId": "s-run",
                            "status": "FAILURE",
                            "tags": [{"key": "phlo/ref", "value": "candidate"}],
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
        assert data["nessie_ref"] == ref
        assert data["dagster_location"] == ("production_jobs" if env == "prod" else "testing_jobs")
        services = {item["id"]: item for item in data["items"]}
        assert services["dagster"]["status"] == "healthy"
        assert services["dagster"]["response_time_seconds"] >= 0
        assert services["nessie"]["status"] == "healthy"
        assert services["trino"] == {
            "id": "trino",
            "status": "unknown",
            "observed_at": None,
            "response_time_seconds": None,
            "runtime_state": "unknown",
            "definition_state": "available",
            "reason": "no_environment_binding",
            "health_origin": None,
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
        ] == "unknown"
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
    assert asyncio.run(v1._runs("production_jobs", "main")) == {"p-run": "STARTED"}
    assert asyncio.run(v1._runs("testing_jobs", "candidate")) == {"s-run": "FAILURE"}


def test_recent_runs_require_ref_even_with_matching_location(client, monkeypatch):
    http, *_ = client
    rows = [
        {
            "runId": "other-ref",
            "status": "FAILURE",
            "tags": [{"key": "phlo/ref", "value": "candidate"}],
            "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
        },
        {
            "runId": "main-ref",
            "status": "STARTED",
            "tags": [{"key": "phlo/ref", "value": "main"}],
            "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
        },
    ]

    async def graphql(url, query):
        return {"data": {"runsOrError": {"__typename": "Runs", "results": rows}}}

    monkeypatch.setattr(v1, "graphql_request", graphql)
    assert asyncio.run(v1._runs("production_jobs", "main")) == {"main-ref": "STARTED"}
    rows[1]["tags"] = []
    monkeypatch.setattr(v1_assets, "_assets", lambda *args, **kwargs: asyncio.sleep(0, result=[]))
    assert http.get("/api/v1/overview?env=prod").status_code == 503


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

    async def runs(location, ref):
        n = next(runs_seen)
        assert ref == ("main" if location == "production_jobs" else "candidate")
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

        async def runs(location, ref):
            assert location == "production_jobs" and ref == "main"
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
            "isMaterializable": True,
            "isPartitioned": False,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [
                {
                    "timestamp": "1780000000000",
                    "runId": "p-run",
                    "partition": None,
                    "runOrError": {
                        "__typename": "Run",
                        "runId": "p-run",
                        "status": "SUCCESS",
                        "tags": [{"key": "phlo/ref", "value": "main"}],
                        "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                    },
                }
            ],
        },
        {
            "id": "s-orders",
            "assetKey": {"path": ["staging_orders"]},
            "description": "staging orders",
            "computeKind": "dbt",
            "groupName": "warehouse",
            "isMaterializable": False,
            "isPartitioned": False,
            "repository": {"name": "repo", "location": {"name": "testing_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [
                {
                    "timestamp": "1781000000000",
                    "runId": "s-run",
                    "partition": None,
                    "runOrError": {
                        "__typename": "Run",
                        "runId": "s-run",
                        "status": "SUCCESS",
                        "tags": [{"key": "phlo/ref", "value": "candidate"}],
                        "repositoryOrigin": {"repositoryLocationName": "testing_jobs"},
                    },
                }
            ],
        },
    ]

    async def graphql(url, query, *args, **kwargs):
        return _asset_inventory_response(nodes)

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


@pytest.mark.parametrize("path", ["assets", "overview", "sources", "layers"])
def test_missing_code_location_is_unavailable_not_an_empty_inventory(client, monkeypatch, path):
    from types import SimpleNamespace

    from phlo_api import incidents

    http, *_ = client
    response = _asset_inventory_response([])
    # Dagster omits repositories from code locations whose servers failed to load.
    response["data"]["repositoriesOrError"]["nodes"] = [
        repository
        for repository in response["data"]["repositoriesOrError"]["nodes"]
        if repository["location"]["name"] == "testing_jobs"
    ]

    async def graphql(*args, **kwargs):
        return response

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(
        incidents, "incident_stats", lambda request, env: IncidentStatsResponse(env=env, counts={})
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda *args, **kwargs: SimpleNamespace(items=[], next_cursor=None),
    )
    missing = http.get(f"/api/v1/{path}?env=prod")
    assert missing.status_code == 503, missing.text
    # An existing, successfully loaded empty location is still a valid empty workspace.
    empty = http.get(f"/api/v1/{path}?env=staging")
    assert empty.status_code == 200, empty.text
    if path == "overview":
        assert empty.json()["asset_count"] == 0
    else:
        assert empty.json()["items"] == []


def test_asset_cursor_is_environment_bound_and_sources_filter_before_page(client, monkeypatch):
    http, _, _, _, _ = client
    nodes = [
        {
            "id": key,
            "assetKey": {"path": [key]},
            "description": key,
            "computeKind": None,
            "groupName": None,
            "isMaterializable": not source,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [],
        }
        for key, source in (("a", False), ("b", True), ("c", True))
    ]

    async def graphql(url, query, *args, **kwargs):
        return _asset_inventory_response(nodes)

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


@pytest.mark.parametrize("provider", ["dlt", "airbyte", "sling"])
def test_sources_include_declared_ingestion_without_disabling_materialization(
    client, monkeypatch, provider
):
    http, *_ = client
    base = {
        "description": None,
        "computeKind": None,
        "groupName": "ingest",
        "isMaterializable": True,
        "repository": {"name": "repo", "location": {"name": "production_jobs"}},
        "dependencyKeys": [],
        "assetMaterializations": [],
    }
    nodes = [
        {**base, "assetKey": {"path": ["a_dlt_not_ingestion"]}, "tags": []},
        {
            **base,
            "assetKey": {"path": ["b_registry"]},
            "tags": [
                {"key": "asset_type", "value": "ingestion"},
                {"key": "provider", "value": provider},
            ],
        },
        {**base, "assetKey": {"path": ["c_external"]}, "isMaterializable": False},
        {
            **base,
            "assetKey": {"path": ["b_registry"]},
            "repository": {"name": "repo", "location": {"name": "testing_jobs"}},
            "tags": [{"key": "asset_type", "value": "transformation"}],
        },
    ]

    async def graphql(url, query, *args, **kwargs):
        return _asset_inventory_response(nodes)

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    response = http.get("/api/v1/sources?env=prod&limit=1")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == ["b_registry"]
    ingestion = response.json()["items"][0]
    assert ingestion["is_source"] is False
    assert "is_ingestion" not in ingestion
    cursor = response.json()["next_cursor"]
    next_page = http.get(f"/api/v1/sources?env=prod&limit=1&cursor={cursor}")
    assert [item["id"] for item in next_page.json()["items"]] == ["c_external"]
    assert next_page.json()["items"][0]["is_source"] is True
    assert http.get("/api/v1/sources?env=staging").json()["items"] == []


def test_shared_asset_key_preserves_verified_repository_history(client, monkeypatch):
    from types import SimpleNamespace

    from phlo_api import incidents

    http, _, _, _, _ = client
    nodes = [
        {
            "id": env,
            "assetKey": {"path": ["orders"]},
            "description": env,
            "computeKind": None,
            "groupName": None,
            "isMaterializable": True,
            "isPartitioned": False,
            "repository": {"name": "repo", "location": {"name": location}},
            "dependencyKeys": [],
            "assetMaterializations": [
                {
                    "timestamp": "1780000000000",
                    "runId": env,
                    "partition": None,
                    "runOrError": {
                        "__typename": "Run",
                        "runId": env,
                        "status": "SUCCESS",
                        "tags": [
                            {"key": "phlo/ref", "value": "main" if env == "prod" else "candidate"}
                        ],
                        "repositoryOrigin": {"repositoryLocationName": location},
                    },
                }
            ],
        }
        for env, location in (("prod", "production_jobs"), ("staging", "testing_jobs"))
    ]

    async def graphql(url, query, *args, **kwargs):
        if "V1AssetRuns" in query:
            return {
                "data": {
                    "runsFeedOrError": {
                        "__typename": "RunsFeedConnection",
                        "results": [
                            {
                                "__typename": "Run",
                                "runId": "prod-run",
                                "status": "SUCCESS",
                                "tags": [{"key": "phlo/ref", "value": "main"}],
                                "creationTime": 1780000000,
                                "startTime": None,
                                "endTime": None,
                                "pipelineName": "orders",
                                "repositoryOrigin": {
                                    "repositoryName": "repo",
                                    "repositoryLocationName": "production_jobs",
                                },
                                "assetSelection": [{"path": ["orders"]}],
                            },
                            {
                                "__typename": "Run",
                                "runId": "staging-run",
                                "status": "SUCCESS",
                                "tags": [{"key": "phlo/ref", "value": "candidate"}],
                                "creationTime": 1780000000,
                                "startTime": None,
                                "endTime": None,
                                "pipelineName": "orders",
                                "repositoryOrigin": {
                                    "repositoryName": "repo",
                                    "repositoryLocationName": "testing_jobs",
                                },
                                "assetSelection": [{"path": ["orders"]}],
                            },
                        ],
                        "cursor": "",
                        "hasMore": False,
                    }
                }
            }
        return _asset_inventory_response(nodes)

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(
        incidents, "incident_stats", lambda request, env: IncidentStatsResponse(env=env, counts={})
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda request, env, limit, cursor: SimpleNamespace(
            items=[{"asset_id": "orders", "freshness_sla_seconds": 60}], next_cursor=None
        ),
    )
    response = http.get("/api/v1/assets?env=prod")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["history_scoped"] is True
    assert item["last_materialization_at"] is not None
    assert item["last_run_id"] == "prod"
    staging = http.get("/api/v1/assets?env=staging")
    assert staging.status_code == 200
    assert staging.json()["items"][0]["history_scoped"] is True
    assert staging.json()["items"][0]["last_run_id"] == "staging"
    run_history = http.get("/api/v1/assets/orders/runs?env=prod")
    assert run_history.status_code == 200
    assert [item["run_id"] for item in run_history.json()["items"]] == ["prod-run"]
    assert http.get("/api/v1/assets/orders?env=prod").json()["description"] == "prod"
    for env in ("prod", "staging"):
        item = http.get(f"/api/v1/assets?env={env}").json()["items"][0]
        assert item["history_scoped"] is True
        assert item["last_materialization_at"] is not None
        overview = http.get(f"/api/v1/overview?env={env}")
        assert overview.status_code == 200
        assert overview.json()["freshness_counts"] == {
            "fresh": 0,
            "stale": 1,
            "unknown": 0,
        }


@pytest.mark.parametrize(
    ("env", "run_location", "status", "partitioned", "event_partition", "trusted", "run_ref"),
    [
        ("prod", "production_jobs", "SUCCESS", False, None, True, "main"),
        ("prod", "testing_jobs", "SUCCESS", False, None, False, "main"),
        ("staging", "production_jobs", "SUCCESS", False, None, False, "candidate"),
        ("prod", "production_jobs", "FAILURE", False, None, False, "main"),
        ("prod", "production_jobs", "SUCCESS", True, "2026-09-25", False, "main"),
        ("prod", "production_jobs", "SUCCESS", False, "2026-09-25", False, "main"),
        ("prod", "production_jobs", "SUCCESS", False, None, False, "candidate"),
        ("prod", "production_jobs", "SUCCESS", False, None, False, None),
    ],
)
def test_asset_and_overview_require_location_success_and_unpartitioned_evidence(
    client, monkeypatch, env, run_location, status, partitioned, event_partition, trusted, run_ref
):
    from types import SimpleNamespace

    from phlo_api import incidents

    http, *_ = client
    current_location = "production_jobs" if env == "prod" else "testing_jobs"
    observed_at = datetime.now(UTC).replace(microsecond=0)
    materialization = {
        "timestamp": str(int(observed_at.timestamp() * 1000)),
        "runId": "historical-run",
        "partition": event_partition,
        "runOrError": {
            "__typename": "Run",
            "runId": "historical-run",
            "status": status,
            "tags": [{"key": "phlo/ref", "value": run_ref}] if run_ref else [],
            "repositoryOrigin": {"repositoryLocationName": run_location},
        },
        "metadataEntries": [
            {"schema": {"columns": [{"name": "from_event", "type": "INT", "description": None}]}}
        ],
    }
    node = {
        "id": "current-definition",
        "assetKey": {"path": ["orders"]},
        "description": None,
        "computeKind": None,
        "groupName": "warehouse",
        "isMaterializable": True,
        "isPartitioned": partitioned,
        "repository": {"name": "repo", "location": {"name": current_location}},
        "dependencyKeys": [],
        "assetMaterializations": [materialization],
        "metadataEntries": [
            {
                "schema": {
                    "columns": [{"name": "from_definition", "type": "BIGINT", "description": None}]
                }
            }
        ],
    }

    async def graphql(url, query, variables=None):
        if "V1AssetDetail" in query:
            return {"data": {"assetNodeOrError": {"__typename": "AssetNode", **node}}}
        return _asset_inventory_response([node])

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(
        incidents,
        "incident_stats",
        lambda request, selected: IncidentStatsResponse(env=selected, counts={}),
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda request, selected, limit, cursor: SimpleNamespace(
            items=[{"asset_id": "orders", "freshness_sla_seconds": 60}], next_cursor=None
        ),
    )

    listed = http.get(f"/api/v1/assets?env={env}")
    detail = http.get(f"/api/v1/assets/orders?env={env}")
    layers = http.get(f"/api/v1/layers?env={env}")
    overview = http.get(f"/api/v1/overview?env={env}")
    for response in (listed, detail, layers, overview):
        assert response.status_code == 200, response.text
    assert (listed.json()["items"][0]["last_run_id"] is not None) is trusted
    assert (detail.json()["schema_observed_at"] is not None) is trusted
    assert detail.json()["columns"][0]["name"] == ("from_event" if trusted else "from_definition")
    assert (layers.json()["items"][0]["latest_materialization_at"] is not None) is trusted
    assert overview.json()["freshness_counts"] == (
        {"fresh": 1, "stale": 0, "unknown": 0}
        if trusted
        else {"fresh": 0, "stale": 0, "unknown": 1}
    )
    if trusted:
        expected_timestamp = observed_at.isoformat().replace("+00:00", "Z")
        assert listed.json()["items"][0]["last_materialization_at"] == expected_timestamp
        assert detail.json()["schema_observed_at"] == expected_timestamp
        assert layers.json()["items"][0]["latest_materialization_at"] == expected_timestamp
        assert overview.json()["latest_materialization_at"] == expected_timestamp


def test_asset_detail_exposes_typed_columns_and_environment_bound_history(client, monkeypatch):
    http, _, _, _, _ = client
    node = {
        "id": "p-orders",
        "assetKey": {"path": ["orders"]},
        "description": "orders",
        "computeKind": "dbt",
        "groupName": "warehouse",
        "isMaterializable": True,
        "isPartitioned": False,
        "repository": {"name": "repo", "location": {"name": "production_jobs"}},
        "dependencyKeys": [],
        "assetMaterializations": [
            {
                "timestamp": "1780000000000",
                "runId": "p-run",
                "partition": None,
                "runOrError": {
                    "__typename": "Run",
                    "runId": "p-run",
                    "status": "SUCCESS",
                    "tags": [{"key": "phlo/ref", "value": "main"}],
                    "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                },
            }
        ],
        "metadataEntries": [],
    }
    detail = {
        **node,
        "assetMaterializations": [
            {
                "timestamp": "1780000000000",
                "runId": "p-run",
                "partition": None,
                "runOrError": {
                    "__typename": "Run",
                    "runId": "p-run",
                    "status": "SUCCESS",
                    "tags": [{"key": "phlo/ref", "value": "main"}],
                    "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                },
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
        return _asset_inventory_response([detail])

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    response = http.get("/api/v1/assets/orders?env=prod")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["columns"] == [
        {"name": "id", "type": "BIGINT", "description": None, "nullable": None}
    ]
    assert payload["schema_observed_at"] is not None
    assert payload["column_lineage"] == {
        "id": [{"asset_key": ["raw", "orders"], "column_name": "id"}]
    }


def test_asset_list_enriches_only_paged_ref_scoped_table_metadata(client, monkeypatch):
    http, *_ = client
    recent_timestamp = str(int(datetime.now(UTC).timestamp() * 1000))
    nodes = [
        {
            "id": name,
            "assetKey": {"path": [name]},
            "description": name,
            "computeKind": None,
            "groupName": None,
            "isMaterializable": True,
            "isPartitioned": False,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "jobNames": ["daily"],
            "metadataEntries": [{"label": "phlo/relation", "text": f"raw.{name}"}],
            "assetMaterializations": [
                {
                    "timestamp": recent_timestamp,
                    "runId": f"{name}-{index}",
                    "partition": None,
                    "runOrError": {
                        "__typename": "Run",
                        "runId": f"{name}-{index}",
                        "status": "SUCCESS",
                        "tags": [{"key": "phlo/ref", "value": "main"}],
                        "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                    },
                    "metadataEntries": [{"label": "rows_inserted", "intValue": 2}],
                }
                for index in range(100 if name == "a" else 0)
            ],
        }
        for name in ("a", "b")
    ]
    calls = []

    async def graphql(*args, **kwargs):
        return _asset_inventory_response(nodes)

    def table_metadata(name, ref):
        calls.append((name, ref))
        return {
            "row_count": 42,
            "size_bytes": 2048,
            "snapshot_id": "9007199254740993",
            "freshness_observed_at": datetime.now(UTC),
            "freshness_reason": None,
        }

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(v1_assets, "_iceberg_asset_metadata", table_metadata)
    first = http.get("/api/v1/assets?env=prod&limit=1")
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert [item["id"] for item in first_payload["items"]] == ["a"]
    assert first_payload["items"][0]["row_count"] == 42
    assert first_payload["items"][0]["size_bytes"] == 2048
    assert first_payload["items"][0]["table_metadata_error"] is None
    assert len(first_payload["items"][0]["materializations"]) == 100
    assert first_payload["items"][0]["materializations"][0]["rows_inserted"] == 2
    assert first_payload["items"][0]["materialization_history_truncated"] is True
    assert first_payload["next_cursor"] is not None
    assert calls == [("raw.a", "main")]

    second = http.get(f"/api/v1/assets?env=prod&limit=1&cursor={first_payload['next_cursor']}")
    assert second.status_code == 200, second.text
    assert [item["id"] for item in second.json()["items"]] == ["b"]
    assert second.json()["items"][0]["last_materialization_at"] is None
    assert second.json()["items"][0]["freshness_source"] == "iceberg_snapshot"
    assert second.json()["items"][0]["freshness_observed_at"] is not None
    assert second.json()["items"][0]["freshness_reason"] == "missing_sla"
    assert calls == [("raw.a", "main"), ("raw.b", "main")]


def test_asset_list_page_stats_have_a_whole_operation_deadline(client, monkeypatch):
    http, *_ = client
    nodes = [
        {
            "id": f"asset-{index:02d}",
            "assetKey": {"path": [f"asset-{index:02d}"]},
            "description": None,
            "computeKind": None,
            "groupName": None,
            "isMaterializable": True,
            "isPartitioned": False,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "jobNames": [],
            "metadataEntries": [{"label": "phlo/relation", "text": f"raw.asset_{index}"}],
            "assetMaterializations": [],
        }
        for index in range(9)
    ]

    async def graphql(*args, **kwargs):
        return _asset_inventory_response(nodes)

    def slow_table_metadata(name, ref):
        time.sleep(0.05)
        return {"row_count": 42, "size_bytes": 2048}

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(v1_assets, "_iceberg_asset_metadata", slow_table_metadata)
    monkeypatch.setattr(v1_assets, "_ASSET_PAGE_STATS_TIMEOUT_SECONDS", 0.01)

    response = http.get("/api/v1/assets?env=prod&limit=9")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["items"]) == 9
    assert all(item["row_count"] is None for item in payload["items"])
    assert all(item["size_bytes"] is None for item in payload["items"])
    assert all("page read time budget" in item["table_metadata_error"] for item in payload["items"])


@pytest.mark.parametrize(
    "env,location,ref,total",
    [
        ("prod", "production_jobs", "main", 31),
        ("staging", "testing_jobs", "candidate", 47),
    ],
)
def test_asset_overview_reads_declared_and_ref_scoped_table_evidence(
    client, monkeypatch, env, location, ref, total
):
    http, *_ = client
    node = {
        "id": "directory-definition",
        "assetKey": {"path": ["directory"]},
        "description": "directory",
        "computeKind": None,
        "kinds": ["table_store", "dlt"],
        "groupName": "registry",
        "isMaterializable": True,
        "isPartitioned": False,
        "repository": {"name": "repo", "location": {"name": location}},
        "dependencyKeys": [],
        "metadataEntries": [
            {"label": "owner", "text": "facilities"},
            {"label": "source_name", "text": "site_directory"},
            {"label": "schema_ref", "text": "DirectoryContract"},
            {"label": "sla", "jsonString": '{"freshness_hours": 744}'},
            {"label": "phlo/relation", "text": "raw.directory"},
        ],
        "assetMaterializations": [
            {
                "timestamp": "1780000000000",
                "runId": "local-run",
                "partition": None,
                "runOrError": {
                    "__typename": "Run",
                    "runId": "local-run",
                    "status": "SUCCESS",
                    "pipelineName": "wap_partition_job",
                    "tags": [{"key": "phlo/ref", "value": ref}],
                    "repositoryOrigin": {"repositoryLocationName": location},
                },
                "metadataEntries": [
                    {"label": "rows_inserted", "intValue": 7},
                    {"label": "rows_deleted", "intValue": 2},
                ],
            }
        ],
    }
    fleet_run = {
        **node["assetMaterializations"][0],
        "timestamp": "1780000001000",
        "runId": "fleet-run",
        "runOrError": {
            "__typename": "Run",
            "runId": "fleet-run",
            "status": "SUCCESS",
            "pipelineName": "daily_fleet_job",
            "tags": [{"key": "phlo/ref", "value": ref}],
            "repositoryOrigin": {"repositoryLocationName": location},
        },
        "metadataEntries": [{"label": "rows_inserted", "intValue": 11}],
    }
    missing_job = {
        **node["assetMaterializations"][0],
        "timestamp": "1780000002000",
        "runId": "unknown-job-run",
        "runOrError": {
            "__typename": "Run",
            "runId": "unknown-job-run",
            "status": "SUCCESS",
            "pipelineName": None,
            "tags": [{"key": "phlo/ref", "value": ref}],
            "repositoryOrigin": {"repositoryLocationName": location},
        },
        "metadataEntries": [{"label": "rows_inserted", "intValue": 3}],
    }
    malformed_job = {
        **node["assetMaterializations"][0],
        "timestamp": "1780000003000",
        "runId": "malformed-job-run",
        "runOrError": {
            "__typename": "Run",
            "runId": "malformed-job-run",
            "status": "SUCCESS",
            "pipelineName": ["not", "a", "job"],
            "tags": [{"key": "phlo/ref", "value": ref}],
            "repositoryOrigin": {"repositoryLocationName": location},
        },
        "metadataEntries": [{"label": "rows_inserted", "intValue": 4}],
    }
    node["assetMaterializations"].extend([fleet_run, missing_job, malformed_job])
    foreign = {
        **node["assetMaterializations"][0],
        "runId": "foreign-run",
        "runOrError": {
            "__typename": "Run",
            "runId": "foreign-run",
            "status": "SUCCESS",
            "tags": [{"key": "phlo/ref", "value": "other"}],
            "repositoryOrigin": {"repositoryLocationName": "foreign_jobs"},
        },
        "metadataEntries": [{"label": "rows_inserted", "intValue": 999}],
    }
    node["assetMaterializations"].insert(0, foreign)

    async def graphql(*args, **kwargs):
        return _asset_inventory_response([node])

    catalog_calls = []

    def table_metadata(name, selected_ref):
        catalog_calls.append((name, selected_ref))
        return {
            "columns": [{"name": "site_id", "type": "string", "description": None}],
            "row_count": total,
            "size_bytes": 4096,
            "sort_order": [],
            "snapshot_id": "9223372036854775806",
        }

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(v1_assets, "_iceberg_asset_metadata", table_metadata, raising=False)
    response = http.get(f"/api/v1/assets/directory?env={env}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["owner"] == "facilities"
    assert payload["source_name"] == "site_directory"
    assert payload["schema_contract"] == "DirectoryContract"
    assert payload["compute_kind"] == "dlt · table_store"
    assert payload["freshness_sla_seconds"] == 744 * 3600
    assert payload["columns"][0]["name"] == "site_id"
    assert payload["schema_source"] == "catalog"
    assert payload["row_count"] == total
    assert payload["size_bytes"] == 4096
    assert payload["current_snapshot_id"] == "9223372036854775806"
    assert payload["sort_order"] == []
    assert payload["materializations"] == [
        {
            "run_id": "local-run",
            "job_id": "wap_partition_job",
            "timestamp": "2026-05-28T20:26:40Z",
            "rows_inserted": 7,
            "rows_deleted": 2,
        },
        {
            "run_id": "fleet-run",
            "job_id": "daily_fleet_job",
            "timestamp": "2026-05-28T20:26:41Z",
            "rows_inserted": 11,
            "rows_deleted": None,
        },
        {
            "run_id": "unknown-job-run",
            "job_id": None,
            "timestamp": "2026-05-28T20:26:42Z",
            "rows_inserted": 3,
            "rows_deleted": None,
        },
        {
            "run_id": "malformed-job-run",
            "job_id": None,
            "timestamp": "2026-05-28T20:26:43Z",
            "rows_inserted": 4,
            "rows_deleted": None,
        },
    ]
    assert catalog_calls == [("raw.directory", ref)]

    def unavailable_metadata(name, selected_ref):
        raise OSError("catalog unavailable")

    monkeypatch.setattr(v1_assets, "_iceberg_asset_metadata", unavailable_metadata)
    response = http.get(f"/api/v1/assets/directory?env={env}")
    assert response.status_code == 200, response.text
    unavailable = response.json()
    assert unavailable["owner"] == "facilities"
    assert unavailable["materializations"] == payload["materializations"]
    assert unavailable["columns"] == []
    assert unavailable["schema_source"] == "unavailable"
    assert unavailable["row_count"] is None
    assert unavailable["size_bytes"] is None
    assert unavailable["sort_order"] is None
    assert "catalog connection" in unavailable["table_metadata_error"]


@pytest.mark.parametrize(
    "deletes,expected_rows",
    [
        ({"total-position-deletes": "0", "total-equality-deletes": "0"}, 31),
        ({"total-position-deletes": "1", "total-equality-deletes": "0"}, None),
        ({"total-position-deletes": "0", "total-equality-deletes": "2"}, None),
        ({}, None),
    ],
)
def test_catalog_row_count_requires_proven_absence_of_deletes(monkeypatch, deletes, expected_rows):
    from types import SimpleNamespace

    from phlo_iceberg import catalog

    table = SimpleNamespace(
        current_snapshot=lambda: SimpleNamespace(
            summary=SimpleNamespace(
                additional_properties={"total-records": "31", "total-files-size": "8192", **deletes}
            )
        ),
        schema=lambda: SimpleNamespace(fields=[]),
        sort_order=lambda: SimpleNamespace(fields=[]),
    )
    monkeypatch.setattr(
        catalog, "get_catalog", lambda **kwargs: SimpleNamespace(load_table=lambda name: table)
    )
    metadata = v1_assets._iceberg_asset_metadata("raw.directory", "candidate")
    assert metadata["row_count"] == expected_rows
    assert metadata["size_bytes"] == 8192


def test_catalog_freshness_uses_selected_ref_and_lossless_snapshot_identity(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from phlo_iceberg import catalog

    calls = []
    tables = {
        "main": SimpleNamespace(
            current_snapshot=lambda: SimpleNamespace(
                snapshot_id=9_223_372_036_854_775_806,
                timestamp_ms=1790942400000,
                summary=None,
            ),
            schema=lambda: SimpleNamespace(fields=[]),
            sort_order=lambda: SimpleNamespace(fields=[]),
        ),
        "candidate": SimpleNamespace(
            current_snapshot=lambda: SimpleNamespace(
                snapshot_id=9_223_372_036_854_775_805,
                timestamp_ms=1790942460000,
                summary=None,
            ),
            schema=lambda: SimpleNamespace(fields=[]),
            sort_order=lambda: SimpleNamespace(fields=[]),
        ),
    }

    def get_catalog(*, ref):
        calls.append(ref)
        return SimpleNamespace(load_table=lambda name: tables[ref])

    monkeypatch.setattr(catalog, "get_catalog", get_catalog)
    prod = v1_assets._iceberg_asset_metadata("raw.device_health_current", "main")
    staging = v1_assets._iceberg_asset_metadata("raw.device_health_current", "candidate")

    assert prod["snapshot_id"] == "9223372036854775806"
    assert staging["snapshot_id"] == "9223372036854775805"
    assert prod["freshness_observed_at"] == datetime.fromtimestamp(1790942400, UTC)
    assert staging["freshness_observed_at"] == datetime.fromtimestamp(1790942460, UTC)
    assert calls == ["main", "candidate"]


@pytest.mark.parametrize(
    "entry",
    [
        {"label": "phlo/owner", "__typename": "NullMetadataEntry"},
        {"label": "phlo/owner", "__typename": "JsonMetadataEntry", "jsonString": "null"},
    ],
)
def test_unset_declared_asset_metadata_stays_unknown(entry):
    assert v1_assets._declared_text([entry], "phlo/owner") is None


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
        return {
            "current_snapshot_id": 91 if ref == "main" else 17,
            "metadata_location": f"{ref}/metadata.json",
            "items": [
                {
                    "snapshot_id": 91 if ref == "main" else 17,
                    "timestamp_ms": 1780000000000,
                    "operation": "append",
                    "summary": {"added-records": "3"},
                    "parent_id": None,
                }
            ],
        }

    monkeypatch.setattr(v1_assets, "_iceberg_history", history)
    prod = http.get("/api/v1/tables/warehouse.orders/snapshots?env=prod&limit=5")
    staging = http.get("/api/v1/tables/warehouse.orders/snapshots?env=staging&limit=5")
    assert prod.status_code == staging.status_code == 200
    assert prod.json()["nessie_ref"] == "main"
    assert staging.json()["nessie_ref"] == "candidate"
    assert prod.json()["items"][0]["snapshot_id"] == 91
    assert staging.json()["items"][0]["snapshot_id"] == 17
    assert staging.json()["current_snapshot_id"] == 17
    assert staging.json()["metadata_location"] == "candidate/metadata.json"
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


def test_asset_preview_uses_exact_environment_catalog_and_ref(client, monkeypatch, tmp_path):
    from phlo_api.api import operation_controls

    http, *_ = client
    calls = []
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    relation = "warehouse.orders"

    async def graphql(query, variables=None):
        return _asset_inventory_response(
            [
                {
                    "id": "orders-id",
                    "assetKey": {"path": ["order_current_state"]},
                    "description": None,
                    "computeKind": "python",
                    "groupName": "warehouse",
                    "isMaterializable": True,
                    "repository": {
                        "name": "repo",
                        "location": {"name": "production_jobs"},
                    },
                    "dependencyKeys": [],
                    "metadataEntries": (
                        [{"label": "target_table", "text": relation}] if relation else []
                    ),
                    "assetMaterializations": [],
                }
            ]
        )

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
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "secret")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "staging-secret")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "iceberg_prod", "nessie_ref": "main"},
                "staging": {"catalog": "iceberg_stage", "nessie_ref": "candidate"},
            }
        ),
    )
    prod = http.get("/api/v1/assets/order_current_state/preview?env=prod&limit=1")
    assert prod.status_code == 200, prod.text
    assert prod.json()["nessie_ref"] == "main"
    assert calls[0][1] == "iceberg_prod"
    assert calls[0][0] == 'SELECT * FROM "iceberg_prod"."warehouse"."orders" LIMIT 2'
    record = json.loads((tmp_path / ".phlo" / "audit" / "operations.jsonl").read_text())
    assert record["operation"] == "v1_asset_preview"
    assert record["payload"] == {
        "env": "prod",
        "asset_id": "order_current_state",
        "nessie_ref": "main",
    }
    assert record["result"] == {"returned_row_count": 1, "has_more": False}
    assert "rows" not in record and "secret" not in json.dumps(record)
    usage = http.get("/api/v1/assets/order_current_state/usage?env=prod")
    assert usage.status_code == 200, usage.text
    assert usage.json()["source"] == "api_preview"
    assert usage.json()["status"] == "partial"
    assert usage.json()["items"][0]["returned_row_count"] == 1

    def unavailable_audit(**kwargs):
        raise OSError("test sink unavailable")

    monkeypatch.setattr(operation_controls, "audit_operation", unavailable_audit)
    assert http.get("/api/v1/assets/order_current_state/preview?env=prod").status_code == 503
    assert len(calls) == 2  # Trino finished; the API refuses to return unrecorded data.
    assert len((tmp_path / ".phlo" / "audit" / "operations.jsonl").read_text().splitlines()) == 1
    relation = ""
    assert http.get("/api/v1/assets/order_current_state/preview?env=prod").status_code == 503
    relation = "warehouse.orders;DELETE"
    assert http.get("/api/v1/assets/order_current_state/preview?env=prod").status_code == 503
    assert len(calls) == 2


def test_asset_relation_declarations_reject_conflicts():
    from phlo_api.errors import BackendUnavailableError

    assert (
        v1_assets._declared_relation([{"label": "target_table", "text": "raw.orders"}])
        == "raw.orders"
    )
    assert (
        v1_assets._declared_relation(
            [
                {"label": "target_table", "text": "raw.orders"},
                {"label": "phlo/relation", "text": "raw.orders"},
            ]
        )
        == "raw.orders"
    )
    for entries in (
        [
            {"label": "target_table", "text": "raw.orders"},
            {"label": "phlo/relation", "text": "raw.other"},
        ],
        [{"label": "target_table", "text": "raw.orders;DROP TABLE raw.orders"}],
        [
            {"label": "target_table", "text": "raw.orders"},
            {"label": "target_table", "text": "raw.other"},
        ],
    ):
        with pytest.raises(BackendUnavailableError):
            v1_assets._declared_relation(entries)


def test_preview_usage_is_ref_scoped_authorized_paginated_and_fail_closed(
    client, monkeypatch, tmp_path
):
    from phlo_api.api.operation_controls import audit_operation

    http, decisions, _, _, backend = client
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_BYTES", "1")

    async def assets(*args, **kwargs):
        return [
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
        ]

    monkeypatch.setattr(v1_assets, "_assets", assets)
    url = "/api/v1/assets/warehouse/orders/usage"
    empty = http.get(f"{url}?env=prod")
    assert empty.status_code == 200
    assert empty.json()["status"] == "unavailable" and empty.json()["items"] == []
    assert empty.json()["reason"] == "no_retained_preview_evidence"

    def access(env, asset, ref, count):
        audit_operation(
            operation="v1_asset_preview",
            target=f"{env}:{asset}@{ref}",
            dry_run=False,
            auth={"subject": "alice", "scopes": []},
            payload={"env": env, "asset_id": asset, "nessie_ref": ref},
            result={"returned_row_count": count, "has_more": False},
        )

    access("prod", "warehouse/orders", "main", 7)
    access("staging", "warehouse/orders", "candidate", 2)
    access("prod", "warehouse/orders", "old-ref", 40)
    access("prod", "warehouse/other", "main", 60)
    access("prod", "warehouse/orders", "main", 9)
    assert (tmp_path / ".phlo" / "audit" / "operations.jsonl.4").exists()
    first = http.get(f"{url}?env=prod&limit=1")
    assert first.status_code == 200, first.text
    assert [item["returned_row_count"] for item in first.json()["items"]] == [9]
    cursor = first.json()["next_cursor"]
    assert cursor
    second = http.get(f"{url}?env=prod&limit=1&cursor={cursor}")
    assert [item["returned_row_count"] for item in second.json()["items"]] == [7]
    assert second.json()["next_cursor"] is None
    assert http.get(f"{url}?env=staging&cursor={cursor}").status_code == 400
    staging = http.get(f"{url}?env=staging")
    assert [item["returned_row_count"] for item in staging.json()["items"]] == [2]
    assert ("asset.read", "env=prod|asset_id=warehouse/orders", "prod") in decisions

    backend.explain_decision = lambda principal, action, resource, context: AuthorizationDecision(
        allowed=False, reason_code="explicit_deny"
    )
    assert http.get(f"{url}?env=prod").status_code == 403
    backend.explain_decision = lambda principal, action, resource, context: AuthorizationDecision(
        allowed=True, reason_code="explicit_allow"
    )
    access("prod", "warehouse/orders", "main", 11)
    assert http.get(f"{url}?env=prod&cursor={cursor}").status_code == 400
    audit_path = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    audit_path.write_text("not-json\n")
    assert http.get(f"{url}?env=prod").status_code == 503
    audit_path.write_text(
        json.dumps(
            {
                "operation": "v1_asset_preview",
                "surface": "phlo-api",
                "payload": [],
                "result": {"returned_row_count": 999, "has_more": False},
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        + "\n"
    )
    assert http.get(f"{url}?env=prod").status_code == 503
    audit_path.write_text(json.dumps({"operation": "unrelated"}) + "\n")
    (tmp_path / ".phlo" / "audit" / "operations.jsonl.1").write_text("invalid-archive\n")
    assert http.get(f"{url}?env=prod").status_code == 503


def test_query_usage_requires_asset_and_physical_table_read_after_reassignment(client, monkeypatch):
    from phlo.security.adapters import EnforcementResult
    from phlo_api.usage import ObservedQuery, QueryUsagePage

    http, decisions, _, _, backend = client

    async def assets(*args, **kwargs):
        return [
            v1_assets.AssetView(
                id=asset_id,
                key=[asset_id],
                description=None,
                compute_kind=None,
                group_name=None,
                is_source=False,
                dependencies=[],
                relation=relation,
                history_scoped=False,
                last_materialization_at=None,
                last_run_id=None,
            )
            for asset_id, relation in (
                ("asset_a", "warehouse.orders"),
                ("asset_b", "warehouse.other"),
            )
        ]

    reads = []

    def read(env, ref, asset_id, table_id, limit, cursor):
        reads.append((env, ref, asset_id, table_id))
        return QueryUsagePage(
            env=env,
            asset_id=asset_id,
            table_name="warehouse.orders",
            nessie_ref=ref,
            status="partial",
            items=[
                ObservedQuery(
                    query_id="b_era_query",
                    source_id="trino",
                    occurred_at=datetime.now(UTC),
                    query_state="FINISHED",
                )
            ],
            next_cursor=None,
        )

    monkeypatch.setattr(v1_assets, "_assets", assets)
    monkeypatch.setattr(v1_assets, "read_query_usage", read)
    table_allowed = False

    def decision(principal, action, resource, context):
        decisions.append((action, resource.resource_id, context.environment))
        allowed = resource.resource_id == "env=prod|asset_id=asset_a" or (
            table_allowed and resource.resource_id == "env=prod|table_name=warehouse.orders"
        )
        return AuthorizationDecision(
            allowed=allowed, reason_code="explicit_allow" if allowed else "explicit_deny"
        )

    backend.explain_decision = decision
    assert http.get("/api/v1/assets/asset_b/query-usage?env=prod").status_code == 403
    url = "/api/v1/assets/asset_a/query-usage?env=prod"
    assert http.get(url).status_code == 403
    assert reads == []
    assert ("asset.read", "env=prod|asset_id=asset_a", "prod") in decisions
    assert ("asset.read", "env=prod|table_name=warehouse.orders", "prod") in decisions
    backend.explain_decision = lambda principal, action, resource, context: (
        AuthorizationDecision(allowed=False, reason_code="backend_unavailable")
        if resource.resource_id == "env=prod|table_name=warehouse.orders"
        else AuthorizationDecision(allowed=True, reason_code="explicit_allow")
    )
    assert http.get(url).status_code == 503
    assert reads == []
    backend.explain_decision = decision
    table_allowed = True
    allowed = http.get(url)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["table_name"] == "warehouse.orders"
    assert allowed.json()["items"][0]["query_id"] == "b_era_query"
    assert reads == [("prod", "main", "asset_a", "warehouse/orders")]
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: True)

    def enforce_call(**kwargs):
        if kwargs["resource"].resource_id == "env=prod|table_name=warehouse.orders":
            return EnforcementResult.deny(reason_code="explicit_deny")
        return EnforcementResult.allow()

    monkeypatch.setattr(security_manifest, "enforce", enforce_call)
    assert http.get(url).status_code == 403
    assert len(reads) == 1
    monkeypatch.setattr(
        security_manifest,
        "enforce",
        lambda **kwargs: (
            EnforcementResult.error(reason_code="backend_unavailable")
            if kwargs["resource"].resource_id == "env=prod|table_name=warehouse.orders"
            else EnforcementResult.allow()
        ),
    )
    assert http.get(url).status_code == 503
    assert len(reads) == 1


def test_asset_preview_refuses_same_key_from_multiple_locations(client, monkeypatch):
    http, *_ = client

    async def graphql(query, variables=None):
        return _asset_inventory_response(
            [
                {
                    "id": f"{location}-orders",
                    "assetKey": {"path": ["warehouse", "orders"]},
                    "description": None,
                    "computeKind": "python",
                    "groupName": "warehouse",
                    "isMaterializable": True,
                    "repository": {"name": "repo", "location": {"name": location}},
                    "dependencyKeys": [],
                    "assetMaterializations": [],
                }
                for location in ("production_jobs", "testing_jobs")
            ]
        )

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
        "isMaterializable": True,
        "repository": {"name": "prod_repo", "location": {"name": "production_jobs"}},
        "jobNames": ["warehouse_job"],
        "dependencyKeys": [],
        "assetMaterializations": [],
    }

    async def graphql(query, variables=None):
        if "V1Assets" in query or "AssetActionContext" in query:
            return _asset_inventory_response([node])
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

    endpoint = "/api/v1/assets/warehouse/orders/materialize?env=prod"
    for change in (
        {"dry_run": False},
        {"partition_key": "2026-09-25"},
        {"run_config": {"ops": {"warehouse_job": {"config": {"size": 2}}}}},
    ):
        conflict = http.post(
            endpoint,
            json={"job_name": "warehouse_job", "idempotency_key": "req-1", **change},
        )
        assert conflict.status_code == 409
    assert len(calls) == 1

    live = http.post(
        endpoint,
        json={"job_name": "warehouse_job", "idempotency_key": "req-live", "dry_run": False},
    )
    assert live.status_code == 200
    assert (
        http.post(
            endpoint,
            json={"job_name": "warehouse_job", "idempotency_key": "req-live"},
        ).status_code
        == 409
    )
    assert len(calls) == 2
    assert len(audit_log.read_text().splitlines()) == 2


def test_backfill_action_requires_bounded_explicit_partitions(client, monkeypatch):
    http, *_ = client

    async def unexpected_graphql(*args, **kwargs):
        raise AssertionError("the single-replica gate must fail before provider discovery")

    monkeypatch.setattr(v1_assets, "_graphql", unexpected_graphql)
    disabled = http.post(
        "/api/v1/assets/warehouse/orders/backfill?env=prod",
        json={
            "job_name": "orders_job",
            "partition_set_name": "daily",
            "partitions": ["2026-09-25"],
            "idempotency_key": "req-2",
        },
    )
    assert disabled.status_code == 503


def test_latest_and_all_backfills_are_environment_pinned_and_bounded(client, monkeypatch, tmp_path):
    http, *_ = client
    from phlo_api.api import operation_controls
    from phlo_api.observatory_api import orchestrator_operations, run_action_contract

    locations = {
        "prod_orders": ("production_jobs", "prod_repo"),
        "stage_orders": ("testing_jobs", "stage_repo"),
    }
    assets = [
        {
            "id": key,
            "assetKey": {"path": ["warehouse", key]},
            "description": None,
            "computeKind": "python",
            "groupName": "warehouse",
            "isMaterializable": True,
            "repository": {"name": repository, "location": {"name": location}},
            "dependencyKeys": [],
            "assetMaterializations": [],
        }
        for key, (location, repository) in locations.items()
    ]
    latest_query_variables = []
    provider_calls = []

    async def graphql(query, variables=None):
        if "V1Assets" in query or "AssetActionContext" in query:
            return _asset_inventory_response(
                [{**asset, "jobNames": ["orders_job"]} for asset in assets]
            )
        key = variables["assetKey"]["path"][-1] if "assetKey" in variables else None
        if "V1AssetDetail" in query:
            location, repository = locations[key]
            return {
                "data": {
                    "assetNodeOrError": {
                        "__typename": "AssetNode",
                        **next(asset for asset in assets if asset["id"] == key),
                        "jobNames": ["orders_job"],
                    }
                }
            }
        if "V1BackfillPartitionSet" in query:
            selector = variables["repositorySelector"]
            return {
                "data": {
                    "partitionSetOrError": {
                        "__typename": "PartitionSet",
                        "pipelineName": "orders_job",
                        "repositoryOrigin": {
                            "repositoryLocationName": selector["repositoryLocationName"],
                            "repositoryName": selector["repositoryName"],
                        },
                    }
                }
            }
        if "AssetOperationNode" in query:
            latest_query_variables.append(variables)
            selector = variables["selector"]
            location = selector["repositoryLocationName"]
            key = "prod_orders" if location == "production_jobs" else "stage_orders"
            prod_selection_count = sum(
                item["selector"]["repositoryLocationName"] == "production_jobs"
                for item in latest_query_variables
            )
            partition_key = "2026-09-26" if prod_selection_count > 1 else "2026-09-25"
            return {
                "data": {
                    "repositoryOrError": {
                        "__typename": "Repository",
                        "name": selector["repositoryName"],
                        "location": {"name": location},
                        "assetNodes": [
                            {
                                **next(asset for asset in assets if asset["id"] == key),
                                "partitionKeyConnection": {
                                    "results": [
                                        partition_key if key == "prod_orders" else "2026-09-24"
                                    ],
                                    "cursor": "",
                                    "hasMore": True,
                                },
                            }
                        ],
                    }
                }
            }
        raise AssertionError("unexpected Dagster query")

    class Provider:
        async def backfill_asset(self, asset_id, request):
            provider_calls.append((asset_id, request))
            return {"accepted": True, "all_partitions": request["all_partitions"]}

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

    prod_latest = http.post(
        "/api/v1/assets/warehouse/prod_orders/backfill?env=prod",
        json={
            "job_name": "orders_job",
            "partition_set_name": "orders_daily",
            "selection": "latest",
            "idempotency_key": "prod-latest",
        },
    )
    stage_latest = http.post(
        "/api/v1/assets/warehouse/stage_orders/backfill?env=staging",
        json={
            "job_name": "orders_job",
            "partition_set_name": "orders_daily",
            "selection": "latest",
            "idempotency_key": "stage-latest",
        },
    )
    prod_latest_replay = http.post(
        "/api/v1/assets/warehouse/prod_orders/backfill?env=prod",
        json={
            "job_name": "orders_job",
            "partition_set_name": "orders_daily",
            "selection": "latest",
            "idempotency_key": "prod-latest",
        },
    )
    full = http.post(
        "/api/v1/assets/warehouse/prod_orders/backfill?env=prod",
        json={
            "job_name": "orders_job",
            "partition_set_name": "orders_daily",
            "selection": "all",
            "idempotency_key": "prod-full",
        },
    )

    assert (
        prod_latest.status_code
        == stage_latest.status_code
        == prod_latest_replay.status_code
        == full.status_code
        == 200
    )
    assert prod_latest_replay.json() == prod_latest.json()
    assert [call[1]["partitions"] for call in provider_calls] == [
        ["2026-09-25"],
        ["2026-09-24"],
        [],
    ]
    assert provider_calls[0][1]["repository_location_name"] == "production_jobs"
    assert provider_calls[0][1]["tags"] == {
        "environment": "prod",
        "phlo/ref": "main",
        "phlo/job": "orders_job",
        "phlo/selection": "latest",
    }
    assert provider_calls[1][1]["repository_location_name"] == "testing_jobs"
    assert provider_calls[1][1]["tags"]["phlo/ref"] == "candidate"
    assert provider_calls[2][1]["all_partitions"] is True
    assert provider_calls[2][1]["repository_location_name"] == "production_jobs"
    assert latest_query_variables == [
        {
            "selector": {
                "repositoryName": "prod_repo",
                "repositoryLocationName": "production_jobs",
            },
            "limit": 1,
        },
        {
            "selector": {"repositoryName": "stage_repo", "repositoryLocationName": "testing_jobs"},
            "limit": 1,
        },
        {
            "selector": {
                "repositoryName": "prod_repo",
                "repositoryLocationName": "production_jobs",
            },
            "limit": 1,
        },
    ]
    assert "partitionKeysByDimension" not in v1_assets._OPERATION_NODE_QUERY

    endpoint = "/api/v1/assets/warehouse/prod_orders/backfill?env=prod"
    dry_request = {
        "job_name": "orders_job",
        "partition_set_name": "orders_daily",
        "selection": "all",
        "idempotency_key": "prod-full",
    }
    assert http.post(endpoint, json={**dry_request, "dry_run": False}).status_code == 409
    live_request = {**dry_request, "idempotency_key": "prod-live", "dry_run": False}
    assert http.post(endpoint, json=live_request).status_code == 200
    assert http.post(endpoint, json={**live_request, "dry_run": True}).status_code == 409
    assert len(provider_calls) == 4


@pytest.mark.parametrize(
    ("partition_response", "expected_status"),
    [
        ({"results": [], "cursor": "", "hasMore": False}, 503),
        ({"results": ["p1", "p2"], "cursor": "next", "hasMore": True}, 502),
        ({"wrong_location": True, "results": ["p1"], "cursor": "", "hasMore": False}, 404),
        ({"wrong_partition_location": True}, 404),
        ({"wrong_job": True}, 404),
        ({"upstream_failure": True}, 502),
    ],
)
def test_latest_backfill_fails_closed_on_empty_oversized_or_wrong_location(
    client, monkeypatch, tmp_path, partition_response, expected_status
):
    http, *_ = client
    from phlo_api.api import operation_controls
    from phlo_api.observatory_api import orchestrator_operations, run_action_contract

    node = {
        "id": "orders",
        "assetKey": {"path": ["warehouse", "orders"]},
        "description": None,
        "computeKind": "python",
        "groupName": "warehouse",
        "isMaterializable": True,
        "repository": {"name": "prod_repo", "location": {"name": "production_jobs"}},
        "dependencyKeys": [],
        "assetMaterializations": [],
    }
    provider_calls = []

    async def graphql(query, variables=None):
        if "V1Assets" in query or "AssetActionContext" in query:
            return _asset_inventory_response([{**node, "jobNames": ["orders_job"]}])
        if "V1AssetDetail" in query:
            return {
                "data": {
                    "assetNodeOrError": {
                        "__typename": "AssetNode",
                        **node,
                        "jobNames": ["orders_job"],
                    }
                }
            }
        if "V1BackfillPartitionSet" in query:
            selector = variables["repositorySelector"]
            if partition_response.get("wrong_partition_location"):
                selector = {**selector, "repositoryLocationName": "testing_jobs"}
            return {
                "data": {
                    "partitionSetOrError": {
                        "__typename": "PartitionSet",
                        "pipelineName": (
                            "other_job" if partition_response.get("wrong_job") else "orders_job"
                        ),
                        "repositoryOrigin": selector,
                    }
                }
            }
        if "AssetOperationNode" in query:
            if partition_response.get("upstream_failure"):
                return {"errors": [{"message": "Dagster unavailable"}]}
            return {
                "data": {
                    "repositoryOrError": {
                        "__typename": "Repository",
                        "name": "prod_repo",
                        "location": {
                            "name": "testing_jobs"
                            if partition_response.get("wrong_location")
                            else "production_jobs"
                        },
                        "assetNodes": [
                            {
                                **node,
                                "partitionKeyConnection": {
                                    key: value
                                    for key, value in partition_response.items()
                                    if key not in {"wrong_location", "upstream_failure"}
                                },
                            }
                        ],
                    }
                }
            }
        raise AssertionError("unexpected Dagster query")

    class Provider:
        async def backfill_asset(self, asset_id, request):
            provider_calls.append(request)
            return {"accepted": True}

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
        "/api/v1/assets/warehouse/orders/backfill?env=prod",
        json={
            "job_name": "orders_job",
            "partition_set_name": "orders_daily",
            "selection": "latest",
            "idempotency_key": "latest-test",
        },
    )
    assert response.status_code == expected_status, response.text
    assert provider_calls == []


@pytest.mark.parametrize(
    ("local_checks", "foreign_checks", "available"),
    [
        (True, True, False),
        (False, True, False),
        (True, False, True),
        (True, None, False),
        (None, False, False),
    ],
)
def test_check_definitions_require_repository_local_uniqueness_proof(
    client, monkeypatch, local_checks, foreign_checks, available
):
    from types import SimpleNamespace

    from phlo_api import incidents

    http, *_ = client
    nodes = [
        {
            "assetKey": {"path": ["shared"]},
            "repository": {"name": "repo", "location": {"name": location}},
            "isMaterializable": location == "production_jobs",
            "dependencyKeys": [],
            "assetMaterializations": [],
            "hasAssetChecks": has_checks,
        }
        for location, has_checks in (
            ("production_jobs", local_checks),
            ("testing_jobs", foreign_checks),
        )
    ]
    definitions_read = []

    async def graphql(url, query, variables=None):
        if "V1Assets" in query:
            assert "hasAssetChecks" in query
            return _asset_inventory_response(nodes)
        if "V1AssetChecks" in query:
            definitions_read.append(variables)
            assert variables["includeLegacy"] is available
            repository_node = {**nodes[0]}
            if variables["includeLegacy"]:
                repository_node["assetChecksOrError"] = {
                    "__typename": "AssetChecks",
                    "checks": [{"name": "local-volume"}]
                    + ([{"name": "foreign-schema"}] if foreign_checks else []),
                }
            return _asset_inventory_response(
                [repository_node],
                repository_selector=variables["repositorySelector"],
            )
        assert "V1AssetCheckExecutions" in query
        return {"data": {"assetCheckExecutions": []}}

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(v1, "_runs", lambda *args: asyncio.sleep(0, result={}))
    monkeypatch.setattr(
        incidents, "incident_stats", lambda request, env: IncidentStatsResponse(env=env, counts={})
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda *args, **kwargs: SimpleNamespace(items=[], next_cursor=None),
    )
    direct = http.get("/api/v1/assets/shared/checks?env=prod")
    assert direct.status_code == (200 if available else 503), direct.text
    overview = http.get("/api/v1/overview?env=prod")
    assert overview.status_code == 200, overview.text
    quality = overview.json()["quality_checks"]
    if available:
        assert [item["name"] for item in direct.json()["definitions"]] == ["local-volume"]
        assert quality["counts"] == {"passing": 0, "total": 1, "unevaluated": 1}
        assert len(definitions_read) == 2
    else:
        assert quality["status"] == "unknown" and quality["counts"] is None
        assert quality["reason"] == "check_definition_scope_unverified"
        assert "Repository-local asset-check export is unavailable" in direct.text
        assert len(definitions_read) == 2
        assert all(
            call["repositorySelector"]
            == {"repositoryLocationName": "production_jobs", "repositoryName": "repo"}
            for call in definitions_read
        )


def test_asset_check_history_filters_duplicate_key_runs_by_location(client, monkeypatch):
    http, _, _, _, _ = client
    check_location = "production_jobs"
    nodes = [
        {
            "assetKey": {"path": ["warehouse", "orders"]},
            "repository": {"name": "repo", "location": {"name": location}},
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
                "success": env == "prod",
                "severity": "ERROR",
                "metadataEntries": [
                    {"__typename": "IntMetadataEntry", "label": "rows", "intValue": count}
                ],
            },
            "run": {
                "runId": run_id,
                "tags": [{"key": "phlo/ref", "value": "main" if env == "prod" else "candidate"}],
                "repositoryOrigin": {
                    "repositoryLocationName": "production_jobs"
                    if env == "prod"
                    else "testing_jobs",
                    "repositoryName": "repo",
                },
            },
        }
        for env, run_id, count in (("prod", "p-run", 9), ("staging", "s-run", 2))
    ]
    executions.append(
        {
            **executions[0],
            "runId": "wrong-ref",
            "run": {
                **executions[0]["run"],
                "runId": "wrong-ref",
                "tags": [{"key": "phlo/ref", "value": "candidate"}],
            },
        }
    )
    executions.append(
        {
            **executions[0],
            "runId": "wrong-repository",
            "run": {
                **executions[0]["run"],
                "runId": "wrong-repository",
                "repositoryOrigin": {
                    "repositoryLocationName": "production_jobs",
                    "repositoryName": "another_repo",
                },
            },
        }
    )
    wrong_location_run_id_mismatch = {
        **executions[0],
        "runId": "wrong-location-execution-id",
        "run": {
            **executions[0]["run"],
            "repositoryOrigin": {
                "repositoryLocationName": "testing_jobs",
                "repositoryName": "repo",
            },
        },
    }
    wrong_repository_run_id_mismatch = {
        **executions[0],
        "runId": "wrong-repository-execution-id",
        "run": {
            **executions[0]["run"],
            "repositoryOrigin": {
                "repositoryLocationName": "production_jobs",
                "repositoryName": "another_repo",
            },
        },
    }
    executions.extend([wrong_location_run_id_mismatch, wrong_repository_run_id_mismatch])
    for row in (wrong_location_run_id_mismatch, wrong_repository_run_id_mismatch):
        normalized, verified = v1_assets._normalize_check_execution(
            row, "production_jobs", "repo", "main", "quality_prod"
        )
        assert normalized is None and not verified
    malformed_run = {**executions[0], "run": ["not a GrapheneRun object"]}
    assert v1_assets._normalize_check_execution(
        malformed_run, "production_jobs", "repo", "main", "quality_prod"
    ) == (None, False)
    executions.append(
        {
            "status": "SUCCEEDED",
            "runId": "missing-origin-sensitive-run",
            "timestamp": 1780000000,
            "checkName": "quality_prod",
            "evaluation": {
                "success": False,
                "severity": "ERROR",
                "metadataEntries": [
                    {"__typename": "TextMetadataEntry", "label": "private", "text": "hidden"}
                ],
            },
            "run": {
                "runId": "missing-origin-sensitive-run",
                "tags": [{"key": "phlo/ref", "value": "main"}],
            },
        }
    )

    async def graphql(url, query, variables=None):
        if "V1Assets" in query:
            return _asset_inventory_response(
                [
                    {
                        "id": env,
                        "assetKey": {"path": ["warehouse", "orders"]},
                        "description": env,
                        "computeKind": None,
                        "groupName": None,
                        "isMaterializable": True,
                        "repository": {"name": "repo", "location": {"name": location}},
                        "hasAssetChecks": location == check_location,
                        "dependencyKeys": [],
                        "assetMaterializations": [],
                    }
                    for env, location in (
                        ("prod", "production_jobs"),
                        ("staging", "testing_jobs"),
                    )
                ]
            )
        if "V1AssetChecks" in query:
            assert variables["repositorySelector"]["repositoryName"] == "repo"
            return _asset_inventory_response(
                nodes, repository_selector=variables["repositorySelector"]
            )
        if "V1AssetCheckExecutions" in query:
            return {
                "data": {
                    "assetCheckExecutions": [
                        row for row in executions if row["checkName"] == variables["checkName"]
                    ]
                }
            }
        raise AssertionError("unexpected Dagster query")

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    prod = http.get("/api/v1/assets/warehouse/orders/checks?env=prod")
    check_location = "testing_jobs"
    staging = http.get("/api/v1/assets/warehouse/orders/checks?env=staging")
    assert prod.status_code == staging.status_code == 200
    assert prod.json()["definitions"] == [{"name": "quality_prod", "description": "prod"}]
    assert staging.json()["definitions"] == [{"name": "quality_staging", "description": "staging"}]
    assert prod.json()["executions"] == []
    assert prod.json()["history"] == {
        "status": "partial",
        "unverifiable_checks": ["quality_prod"],
    }
    assert [item["run_id"] for item in staging.json()["executions"]] == ["s-run"]
    assert "missing-origin-sensitive-run" not in prod.text
    assert "hidden" not in prod.text
    assert staging.json()["history"] == {"status": "complete", "unverifiable_checks": []}
    assert staging.json()["executions"][0]["passed"] is False
    assert staging.json()["executions"][0]["metadata"] == [{"label": "rows", "value": 2}]
    check_location = "production_jobs"
    executions[0]["run"]["tags"] = []
    malformed_ref = http.get("/api/v1/assets/warehouse/orders/checks?env=prod")
    assert malformed_ref.status_code == 200
    assert malformed_ref.json()["history"]["status"] == "partial"


def test_local_check_export_resolves_duplicate_repository_keys_and_scopes_history(
    client, monkeypatch
):
    from types import SimpleNamespace

    import hashlib

    http, *_ = client
    key = ["shared"]

    def export(checks):
        checks = sorted(checks, key=lambda item: item["name"])
        payload = {"version": 1, "asset_key": key, "complete": True, "checks": checks}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["digest"] = hashlib.sha256(encoded).hexdigest()
        return {"label": "phlo/asset-check-inventory", "jsonString": json.dumps(payload)}

    repositories = [
        ("iot_prod", "production_jobs", "main", ["shared_quality", "production_only"]),
        ("iot_staging", "testing_jobs", "candidate", ["shared_quality", "staging_only"]),
    ]
    nodes = [
        {
            "assetKey": {"path": key},
            "repository": {"name": name, "location": {"name": location}},
            "isMaterializable": True,
            "dependencyKeys": [],
            "assetMaterializations": [],
            "hasAssetChecks": True,
            "metadataEntries": [
                export(
                    [
                        {"name": check_name, "description": f"{name}:{check_name}"}
                        for check_name in checks
                    ]
                )
            ],
        }
        for name, location, _, checks in repositories
    ]

    def execution(name, location, ref, check_name, passed, run_id):
        return {
            "status": "SUCCEEDED",
            "runId": run_id,
            "timestamp": 1780000000,
            "checkName": check_name,
            "evaluation": {"success": passed, "severity": "ERROR", "metadataEntries": []},
            "run": {
                "runId": run_id,
                "tags": [{"key": "phlo/ref", "value": ref}],
                "repositoryOrigin": {"repositoryName": name, "repositoryLocationName": location},
            },
        }

    history = [
        execution("iot_prod", "production_jobs", "main", "shared_quality", True, "prod-shared"),
        execution(
            "iot_staging", "testing_jobs", "candidate", "shared_quality", False, "stage-shared"
        ),
        execution("iot_prod", "production_jobs", "main", "production_only", True, "prod-only"),
        execution("iot_staging", "testing_jobs", "candidate", "staging_only", False, "stage-only"),
        execution("other_repo", "production_jobs", "main", "shared_quality", False, "wrong-repo"),
        execution("iot_prod", "production_jobs", "candidate", "shared_quality", False, "wrong-ref"),
    ]

    async def graphql(url, query, variables=None):
        if "V1Assets" in query:
            return _asset_inventory_response(nodes)
        if "V1AssetChecks" in query:
            assert "@include(if: $includeLegacy)" in query
            assert variables["includeLegacy"] is False
            selector = variables["repositorySelector"]
            selected = next(
                node for node in nodes if node["repository"]["name"] == selector["repositoryName"]
            )
            # A collision-prone native check resolver is deliberately omitted.
            return _asset_inventory_response(
                [selected],
                repository_selector=selector,
            )
        if "V1AssetCheckExecutions" in query:
            return {
                "data": {
                    "assetCheckExecutions": [
                        row for row in history if row["checkName"] == variables["checkName"]
                    ]
                }
            }
        raise AssertionError("unexpected Dagster query")

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    from phlo_api import incidents

    monkeypatch.setattr(v1, "_runs", lambda *args: asyncio.sleep(0, result={}))
    monkeypatch.setattr(
        incidents,
        "incident_stats",
        lambda request, env: IncidentStatsResponse(env=env, counts={"open": 0}),
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda *args, **kwargs: SimpleNamespace(items=[], next_cursor=None),
    )
    prod = http.get("/api/v1/assets/shared/checks?env=prod")
    staging = http.get("/api/v1/assets/shared/checks?env=staging")
    assert prod.status_code == staging.status_code == 200, (
        f"prod={prod.text}; staging={staging.text}"
    )
    assert prod.json()["definitions"] == [
        {"name": "production_only", "description": "iot_prod:production_only"},
        {"name": "shared_quality", "description": "iot_prod:shared_quality"},
    ]
    assert staging.json()["definitions"] == [
        {"name": "shared_quality", "description": "iot_staging:shared_quality"},
        {"name": "staging_only", "description": "iot_staging:staging_only"},
    ]
    assert {row["run_id"] for row in prod.json()["executions"]} == {
        "prod-shared",
        "prod-only",
    }
    assert {row["run_id"] for row in staging.json()["executions"]} == {
        "stage-shared",
        "stage-only",
    }
    production_overview = http.get("/api/v1/overview?env=prod")
    staging_overview = http.get("/api/v1/overview?env=staging")
    assert production_overview.status_code == staging_overview.status_code == 200
    assert production_overview.json()["quality_checks"]["counts"] == {
        "passing": 2,
        "total": 2,
        "unevaluated": 0,
    }
    assert staging_overview.json()["quality_checks"]["counts"] == {
        "passing": 0,
        "total": 2,
        "unevaluated": 0,
    }


def test_local_check_export_rejects_missing_malformed_and_incomplete_proofs():
    import hashlib

    from phlo_api.api.v1_assets import _local_check_export
    from phlo_api.errors import BackendUnavailableError, BadGatewayError

    key = ["orders"]

    def node(payload):
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload["digest"] = hashlib.sha256(encoded).hexdigest()
        return {
            "metadataEntries": [
                {
                    "label": "phlo/asset-check-inventory",
                    "jsonString": json.dumps(payload),
                }
            ]
        }

    assert _local_check_export({}, key) is None
    assert _local_check_export({"metadataEntries": []}, key) is None
    with pytest.raises(BadGatewayError):
        _local_check_export({"metadataEntries": None}, key)
    with pytest.raises(BadGatewayError):
        _local_check_export(
            {"metadataEntries": [{"label": "phlo/asset-check-inventory", "jsonString": "{"}]},
            key,
        )
    with pytest.raises(BackendUnavailableError):
        _local_check_export(
            node({"version": 1, "asset_key": key, "complete": False, "checks": []}), key
        )
    for version in (True, 1.0):
        with pytest.raises(BadGatewayError):
            _local_check_export(
                node({"version": version, "asset_key": key, "complete": True, "checks": []}),
                key,
            )
    with pytest.raises(BackendUnavailableError):
        _local_check_export(
            {
                "metadataEntries": [
                    {
                        "label": "phlo/asset-check-inventory",
                        "jsonString": " " * (16_385),
                    }
                ]
            },
            key,
        )


def test_asset_run_history_uses_asset_selection_location_and_feed_cursor(client, monkeypatch):
    http, *_ = client
    feed_cursors = []
    missing_ref = False
    asset_nodes = [
        {
            "id": env,
            "assetKey": {"path": ["warehouse", "orders"]},
            "jobNames": ["orders"],
            "description": env,
            "computeKind": None,
            "groupName": None,
            "isMaterializable": True,
            "repository": {"name": "repo", "location": {"name": location}},
            "dependencyKeys": [],
            "assetMaterializations": [],
        }
        for env, location in (("prod", "production_jobs"), ("staging", "testing_jobs"))
    ]

    def run(run_id, location, selection, status="SUCCESS", ref=None, repository="repo"):
        return {
            "__typename": "Run",
            "runId": run_id,
            "status": status,
            "tags": [
                {
                    "key": "phlo/ref",
                    "value": ref or ("main" if location == "production_jobs" else "candidate"),
                }
            ],
            "creationTime": 1780000000,
            "startTime": 1780000001,
            "endTime": 1780000002,
            "repositoryOrigin": {
                "repositoryName": repository,
                "repositoryLocationName": location,
            },
            "pipelineName": "orders",
            "assetSelection": [{"path": selection}],
        }

    def whole_job(run_id, location, repository_name="repo"):
        return {
            "__typename": "Run",
            "runId": run_id,
            "status": "SUCCESS",
            "tags": [{"key": "phlo/ref", "value": "main"}],
            "creationTime": 1780000000,
            "startTime": 1780000001,
            "endTime": 1780000002,
            "repositoryOrigin": {
                "repositoryName": repository_name,
                "repositoryLocationName": location,
            },
            "pipelineName": "orders",
            "assetSelection": None,
        }

    async def graphql(url, query, variables=None):
        if "V1Assets" in query:
            return _asset_inventory_response(asset_nodes)
        if "V1AssetRuns" in query:
            feed_cursors.append(variables["cursor"])
            results_by_cursor = {
                None: [
                    run("prod-success", "production_jobs", ["warehouse", "orders"]),
                    run("wrong-ref", "production_jobs", ["warehouse", "orders"], ref="candidate"),
                ],
                "dagster-page-1": [
                    whole_job("whole-job", "production_jobs"),
                    run(
                        "foreign-repo",
                        "production_jobs",
                        ["warehouse", "orders"],
                        repository="foreign",
                    ),
                ],
                "dagster-page-2": [
                    run("prod-failure", "production_jobs", ["warehouse", "orders"], "FAILURE"),
                ],
            }
            results = results_by_cursor[variables["cursor"]]
            if missing_ref:
                results[0]["tags"] = []
            next_cursor = {
                None: "dagster-page-1",
                "dagster-page-1": "dagster-page-2",
                "dagster-page-2": "dagster-page-3",
            }[variables["cursor"]]
            return {
                "data": {
                    "runsFeedOrError": {
                        "__typename": "RunsFeedConnection",
                        "results": results,
                        "cursor": next_cursor,
                        "hasMore": variables["cursor"] != "dagster-page-2",
                    }
                }
            }
        raise AssertionError("unexpected Dagster query")

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    first = http.get("/api/v1/assets/warehouse/orders/runs?env=prod&limit=2")
    assert first.status_code == 200, first.text
    assert [item["run_id"] for item in first.json()["items"]] == ["prod-success"]
    assert first.json()["items"][0]["job_id"] == "orders"
    assert first.json()["items"][0]["duration_seconds"] == 1
    assert first.json()["items"][0]["selected_assets"] == [["warehouse", "orders"]]
    cursor = first.json()["next_cursor"]
    second = http.get(f"/api/v1/assets/warehouse/orders/runs?env=prod&limit=2&cursor={cursor}")
    assert second.status_code == 200, second.text
    assert [item["run_id"] for item in second.json()["items"]] == ["whole-job"]
    assert second.json()["items"][0]["selected_assets"] == []
    third = http.get(
        f"/api/v1/assets/warehouse/orders/runs?env=prod&limit=2&cursor={second.json()['next_cursor']}"
    )
    assert third.status_code == 200, third.text
    assert [item["run_id"] for item in third.json()["items"]] == ["prod-failure"]
    assert third.json()["items"][0]["status"] == "FAILURE"
    assert third.json()["next_cursor"] is None
    assert feed_cursors == [None, "dagster-page-1", "dagster-page-2"]
    assert (
        http.get(f"/api/v1/assets/warehouse/orders/runs?env=staging&cursor={cursor}").status_code
        == 400
    )
    missing_ref = True
    assert http.get("/api/v1/assets/warehouse/orders/runs?env=prod").status_code == 503


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
            "isMaterializable": True,
            "isPartitioned": False,
            "repository": {"name": "repo", "location": {"name": "production_jobs"}},
            "dependencyKeys": [],
            "assetMaterializations": [
                {
                    "timestamp": "1780000000000",
                    "runId": "run-1",
                    "partition": None,
                    "runOrError": {
                        "__typename": "Run",
                        "runId": "run-1",
                        "status": "SUCCESS",
                        "tags": [{"key": "phlo/ref", "value": "main"}],
                        "repositoryOrigin": {"repositoryLocationName": "production_jobs"},
                    },
                }
            ]
            if key == "orders"
            else [],
        }
        for key in ("orders", "unmaterialized")
    ]

    async def graphql(url, query, variables=None):
        if "V1AssetChecks" in query:
            return _asset_inventory_response(
                nodes, repository_selector=variables["repositorySelector"]
            )
        return _asset_inventory_response(nodes)

    from phlo_api import incidents

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(
        incidents,
        "incident_stats",
        lambda request, env: IncidentStatsResponse(env=env, counts={"open": 2}),
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
    assert body["quality_checks"] == {
        "status": "unknown",
        "counts": None,
        "failing_assets": None,
        "reason": "check_definition_scope_unverified",
    }


def test_overview_check_counts_are_location_scoped_and_exclude_runless(client, monkeypatch):
    from types import SimpleNamespace

    http, *_ = client
    nodes = [
        {
            "id": env,
            "assetKey": {"path": ["warehouse", "orders"]},
            "description": env,
            "computeKind": None,
            "groupName": "warehouse",
            "isMaterializable": True,
            "repository": {"name": "repo", "location": {"name": location}},
            "hasAssetChecks": location == "production_jobs",
            "dependencyKeys": [],
            "assetMaterializations": [],
        }
        for env, location in (("prod", "production_jobs"), ("staging", "testing_jobs"))
    ]
    unverifiable = {
        "status": "SUCCEEDED",
        "runId": "unverified-history-run",
        "timestamp": 1780000300,
        "evaluation": {"success": True, "severity": "ERROR", "metadataEntries": []},
        "run": {"runId": "unverified-history-run", "tags": [{"key": "phlo/ref", "value": "main"}]},
    }
    include_unverifiable = False

    async def graphql(url, query, variables=None):
        if "V1Assets" in query:
            return _asset_inventory_response(nodes)
        if "V1AssetChecks" in query:
            return _asset_inventory_response(
                [
                    {
                        "assetKey": {"path": ["warehouse", "orders"]},
                        "repository": {"name": "repo", "location": {"name": location}},
                        "assetChecksOrError": {
                            "__typename": "AssetChecks",
                            "checks": [
                                {"name": "freshness", "description": None},
                                {"name": "volume", "description": None},
                            ],
                        },
                    }
                    for location in ("production_jobs", "testing_jobs")
                ],
                repository_selector=variables["repositorySelector"],
            )
        if "V1AssetCheckExecutions" in query:
            check_name = variables["checkName"]
            check_executions = {
                "freshness": (
                    ("prod-pass", 1780000200, True, "production_jobs"),
                    ("stage-fail", 1780000250, False, "testing_jobs"),
                ),
                "volume": (
                    ("prod-fail", 1780000260, False, "production_jobs"),
                    ("stage-pass", 1780000270, True, "testing_jobs"),
                ),
            }[check_name]
            return {
                "data": {
                    "assetCheckExecutions": [
                        {
                            "status": "SUCCEEDED",
                            "runId": run_id,
                            "timestamp": timestamp,
                            "evaluation": {
                                "success": passed,
                                "severity": "ERROR",
                                "metadataEntries": [],
                            },
                            "run": {
                                "runId": run_id,
                                "tags": [
                                    {
                                        "key": "phlo/ref",
                                        "value": "main"
                                        if location == "production_jobs"
                                        else "candidate",
                                    }
                                ],
                                "repositoryOrigin": {
                                    "repositoryLocationName": location,
                                    "repositoryName": "repo",
                                },
                            },
                        }
                        for run_id, timestamp, passed, location in check_executions
                    ]
                    + ([unverifiable] if include_unverifiable else [])
                }
            }
        raise AssertionError("unexpected Dagster query")

    from phlo_api import incidents
    from phlo_api.api import v1

    monkeypatch.setattr(v1_assets, "graphql_request", graphql)
    monkeypatch.setattr(v1, "_runs", lambda location, ref: asyncio.sleep(0, result={}))
    monkeypatch.setattr(
        incidents,
        "incident_stats",
        lambda request, env: IncidentStatsResponse(env=env, counts={"open": 0}),
    )
    monkeypatch.setattr(
        incidents,
        "list_asset_incident_policies",
        lambda request, env, limit, cursor: SimpleNamespace(items=[], next_cursor=None),
    )

    response = http.get("/api/v1/overview?env=prod")
    assert response.status_code == 200, response.text
    assert response.json()["quality_checks"] == {
        "status": "available",
        "counts": {"passing": 1, "total": 2, "unevaluated": 0},
        "failing_assets": ["warehouse/orders"],
        "reason": None,
    }
    include_unverifiable = True
    unknown = http.get("/api/v1/overview?env=prod")
    assert unknown.status_code == 200
    assert unknown.json()["quality_checks"] == {
        "status": "unknown",
        "counts": None,
        "failing_assets": None,
        "reason": "check_history_scope_unverified",
    }


def test_audit_proposal_generator_emits_only_validated_declarative_checks():
    payload = AssetAuditProposalRequest.model_validate(
        {
            "check_name": "orders_quality",
            "rules": [
                {"kind": "unique", "column": "order_id"},
                {"kind": "range", "column": "total", "minimum": 0, "maximum": 1000},
            ],
            "idempotency_key": "proposal-1",
        }
    )

    path, source = generate_check_file(["warehouse", "orders"], payload.check_name, payload.rules)

    assert path == "workflows/quality/warehouse_orders_orders_quality.py"
    assert 'table="warehouse.orders"' in source
    assert 'UniqueCheck(columns=["order_id"])' in source
    assert 'RangeCheck(column="total", min_value=0.0, max_value=1000.0)' in source
    assert "exec(" not in source


@pytest.mark.parametrize(
    "payload",
    [
        {
            "check_name": "unsafe-name",
            "rules": [{"kind": "unique", "column": "id"}],
            "idempotency_key": "proposal-1",
        },
        {
            "check_name": "valid",
            "rules": [{"kind": "range", "column": "id", "minimum": 10, "maximum": 1}],
            "idempotency_key": "proposal-1",
        },
        {
            "check_name": "valid",
            "rules": [{"kind": "custom_sql", "sql": "drop table orders"}],
            "idempotency_key": "proposal-1",
        },
    ],
)
def test_audit_proposal_rejects_unsafe_declarations(payload):
    with pytest.raises(ValueError):
        AssetAuditProposalRequest.model_validate(payload)


def test_audit_proposal_is_audited_idempotent_and_reviewable_without_git_integration(
    client, tmp_path, monkeypatch
):
    http, *_ = client
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "required")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"writer":{"subject":"alice","scopes":["project:write"]},'
        '"reader":{"subject":"reader","scopes":["project:read"]}}',
    )

    async def assets(*args, **kwargs):
        return [
            v1_assets.AssetView(
                id="warehouse/orders",
                key=["warehouse", "orders"],
                description=None,
                compute_kind="sql",
                group_name="warehouse",
                is_source=False,
                dependencies=[],
                last_materialization_at=None,
                last_run_id=None,
            )
        ]

    async def detail(*args, **kwargs):
        return v1_assets.AssetDetail(
            id="warehouse/orders",
            key=["warehouse", "orders"],
            description=None,
            compute_kind="sql",
            group_name="warehouse",
            is_source=False,
            dependencies=[],
            last_materialization_at=None,
            last_run_id=None,
            columns=[v1_assets.AssetColumn(name="order_id", type="string", description=None)],
            schema_observed_at=None,
        )

    monkeypatch.setattr(v1_assets, "_assets", assets)
    monkeypatch.setattr(v1_assets, "v1_asset_detail", detail)
    body = {
        "check_name": "orders_quality",
        "rules": [{"kind": "unique", "column": "order_id"}],
        "idempotency_key": "review-1",
    }
    headers = {"Authorization": "Bearer writer"}
    url = "/api/v1/assets/warehouse/orders/audits?env=prod"

    denied = http.post(
        url,
        json=body,
        headers={"Authorization": "Bearer reader"},
    )
    assert denied.status_code == 403

    first = http.post(url, json=body, headers=headers)
    replay = http.post(url, json=body, headers=headers)

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    proposal = first.json()
    assert proposal["status"] == "pending_review"
    assert proposal["env"] == "prod"
    assert proposal["nessie_ref"] == "main"
    assert proposal["file_path"] == "workflows/quality/warehouse_orders_orders_quality.py"
    assert proposal["source_digest"]
    assert proposal["patch"].startswith("--- /dev/null\n+++ b/workflows/quality/")
    assert 'UniqueCheck(columns=["order_id"])' in proposal["patch"]
    audit_path = tmp_path / ".phlo" / "audit" / "operations.jsonl"
    records = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    assert '"status": "pending_review"' in records[0]
    assert not (tmp_path / "workflows").exists()

    retrieved = http.get(
        f"/api/v1/assets/warehouse/orders/audits/{proposal['proposal_id']}?env=prod",
        headers=headers,
    )
    assert retrieved.status_code == 200
    assert retrieved.json() == proposal
    wrong_env = http.get(
        f"/api/v1/assets/warehouse/orders/audits/{proposal['proposal_id']}?env=staging",
        headers=headers,
    )
    assert wrong_env.status_code == 404

    publish_url = (
        f"/api/v1/assets/warehouse/orders/audits/{proposal['proposal_id']}/pull-request?env=prod"
    )
    for name in (
        "PHLO_V1_GIT_REVIEW_REPOSITORY",
        "PHLO_V1_GIT_REVIEW_BASE_BRANCH",
        "PHLO_V1_GIT_REVIEW_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    unconfigured = http.post(
        publish_url,
        json={"idempotency_key": "draft-pr-unconfigured"},
        headers=headers,
    )
    assert unconfigured.status_code == 503
    assert "not configured" in unconfigured.json()["error"]["message"]

    monkeypatch.setenv("PHLO_V1_GIT_REVIEW_REPOSITORY", "project-data/orders")
    monkeypatch.setenv("PHLO_V1_GIT_REVIEW_BASE_BRANCH", "main")
    monkeypatch.setenv("PHLO_V1_GIT_REVIEW_TOKEN", "test-only-token")

    async def publish_pr(client, config, audit_proposal):
        assert config.repository == "project-data/orders"
        assert audit_proposal.proposal_id == proposal["proposal_id"]
        return AssetAuditDraftPullRequest(
            proposal_id=audit_proposal.proposal_id,
            repository=config.repository,
            base_branch=config.base_branch,
            head_branch=f"phlo/asset-check-{audit_proposal.proposal_id}",
            pull_request_number=42,
            pull_request_url="https://github.com/project-data/orders/pull/42",
        )

    monkeypatch.setattr(v1_assets, "publish_project_draft_pr", publish_pr)
    published = http.post(
        publish_url,
        json={"idempotency_key": "draft-pr-1"},
        headers=headers,
    )
    published_replay = http.post(
        publish_url,
        json={"idempotency_key": "draft-pr-1"},
        headers=headers,
    )
    assert published.status_code == published_replay.status_code == 202
    assert published.json() == published_replay.json()
    assert published.json()["status"] == "pending_review"
    assert published.json()["pull_request_url"] == "https://github.com/project-data/orders/pull/42"
    records = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(records) == 2

    changed = {**body, "check_name": "another_quality"}
    conflict = http.post(url, json=changed, headers=headers)
    assert conflict.status_code == 409

    def fail_storage(**kwargs):
        raise RuntimeError("storage backend details must not reach clients")

    monkeypatch.setattr(v1_assets, "create_audit_proposal", fail_storage)
    storage_failure = http.post(
        url,
        json={**body, "idempotency_key": "review-storage-failure"},
        headers=headers,
    )
    assert storage_failure.status_code == 503
    assert storage_failure.json()["error"]["message"] == "Audit proposal storage is unavailable."
    assert "storage backend details" not in storage_failure.text
