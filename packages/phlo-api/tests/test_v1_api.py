"""HTTP and event-stream contracts for the first environment-scoped API slice."""

from __future__ import annotations

import asyncio
import itertools
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api.api import v1
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


def test_invalid_selection_missing_identity_policy_and_mapping_fail_closed(client):
    http, decisions, _, monkeypatch, backend_provider = client
    for path in ("/api/v1/services", "/api/v1/services?env=other", "/api/v1/events?env=other"):
        response = http.get(path)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable_input"
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
