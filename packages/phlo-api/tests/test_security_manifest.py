"""Mechanical coverage and access-matrix tests for the mandatory API boundary.

Every registered route must appear in the security manifest with a canonical
action, and an unclassified route fails validation. Authorization cases pin the
failure semantics: anonymous requests are rejected before handlers reach the
application, unregulated surfaces pass through, and principals are checked
against the resolved resource identity with correlation context attached.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from phlo.capabilities.interfaces import AuthPrincipal, AuthorizationDecision, Principal
from phlo.security.adapters import EnforcementResult
from phlo_api.main import app
from phlo_api.security_manifest import (
    HTTP_ROUTE_MANIFEST,
    _route_for_request,
    OperationSpec,
    resolve_resource,
    validate_manifest,
    validate_operation_registry,
)


class _Backend:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, str]] = []
        self.contexts = []

    def explain_decision(self, principal, action, resource, context=None):  # noqa: ANN001
        self.calls.append((principal.subject, action, resource.resource_id))
        self.contexts.append(context)
        return AuthorizationDecision(
            allowed=self.allowed,
            reason_code="explicit_allow" if self.allowed else "default_deny",
            policy_id="test-policy" if self.allowed else None,
            explanation="test decision",
        )


def _principal(subject: str = "viewer") -> tuple[AuthPrincipal, Principal]:
    auth = AuthPrincipal(subject=subject, principal_type="user", groups=())
    canonical = Principal(subject=subject, principal_type="user", roles=(subject,))
    return auth, canonical


@pytest.fixture(autouse=True)
def _regulated_manifest_boundary(monkeypatch):
    """Keep existing manifest authorization cases explicitly regulated."""
    from phlo_api import security_manifest

    monkeypatch.setattr(security_manifest, "is_regulated", lambda: True)

    def enforce_with_test_backend(*, principal, action, resource, context, **_kwargs):  # noqa: ANN001
        backend = security_manifest.get_authorization_backend()
        if backend is None:
            return EnforcementResult.error(reason_code="backend_unavailable")
        decision = backend.explain_decision(
            Principal(
                subject=principal.subject,
                principal_type=principal.principal_type,
                roles=principal.groups,
            ),
            action,
            resource,
            context,
        )
        if decision.allowed:
            return EnforcementResult.allow()
        return EnforcementResult.deny(reason_code=decision.reason_code)

    monkeypatch.setattr(security_manifest, "enforce", enforce_with_test_backend)


def test_http_route_manifest_covers_every_registered_route() -> None:
    resolved = validate_manifest(app)
    actual_operations = {
        (method, route.path)
        for route in app.routes
        if getattr(route, "methods", None)
        for method in route.methods
    }
    resolved_operations = {(method, spec.path) for spec in resolved for method in spec.methods}
    assert resolved_operations == actual_operations
    assert len(HTTP_ROUTE_MANIFEST) == len(
        {getattr(route, "name", None) for route in app.routes if getattr(route, "methods", None)}
    )


def test_non_http_registry_is_complete_and_unique() -> None:
    validate_operation_registry()
    assert all(spec.surface == "http" for spec in HTTP_ROUTE_MANIFEST.values())
    assert not any(
        name.startswith(("hasura.", "dagster.graphql", "hasura.websocket"))
        for name in HTTP_ROUTE_MANIFEST
    )


def test_regulated_surface_adapter_is_derived_from_http_manifest() -> None:
    from phlo_api.regulated_surface_adapter import PhloAPIRegulatedSurfaceAdapter

    manifest_pairs = {(spec.action, spec.resource_type) for spec in HTTP_ROUTE_MANIFEST.values()}
    operations = PhloAPIRegulatedSurfaceAdapter().list_operations()
    adapter_pairs = {(operation["action"], operation["resource_type"]) for operation in operations}

    assert adapter_pairs == manifest_pairs
    assert all(operation["operation_name"].startswith("http.") for operation in operations)


def test_read_surfaces_use_their_specific_canonical_actions() -> None:
    assert HTTP_ROUTE_MANIFEST["check_connection"].action == "observability.read"
    assert HTTP_ROUTE_MANIFEST["get_observatory_preferences"].action == "settings.read"
    assert HTTP_ROUTE_MANIFEST["get_observatory_settings"].action == "settings.read"
    assert HTTP_ROUTE_MANIFEST["get_observatory_dataset_workflow_config"].action == "settings.read"
    assert HTTP_ROUTE_MANIFEST["get_observatory_search"].action == "admin.read"
    assert HTTP_ROUTE_MANIFEST["get_observatory_run_report"].action == "run.read"
    assert HTTP_ROUTE_MANIFEST["get_observatory_run_report"].resource_keys == (
        "project_id",
        "run_id",
        "attempt",
    )
    assert HTTP_ROUTE_MANIFEST["get_observatory_row_journey"].resource_keys == (
        "table_id",
        "row_id",
    )


def test_loki_routes_keep_auth_and_reject_override_parameter() -> None:
    """Loki routes remain classified; request loki_url is not part of auth identity."""
    resolved = {spec.operation_name: spec for spec in validate_manifest(app)}
    for name in (
        "check_connection",
        "query_logs",
        "query_run_logs",
        "stream_run_logs",
        "query_asset_logs",
        "get_log_labels",
    ):
        spec = resolved[name]
        assert "loki_url" not in spec.resource_keys
        assert spec.action in {"observability.read", "audit.read"}
        assert spec.path is not None
        assert spec.path.startswith("/api/loki")


def test_authoring_writes_are_honestly_project_scoped() -> None:
    for name in ("create_workflow", "validate_workflow", "validate_schema", "lint_project"):
        spec = HTTP_ROUTE_MANIFEST[name]
        assert spec.resource_type == "project"
        assert spec.resource_keys == ()


def test_path_resource_identity_includes_every_scoped_read_key() -> None:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/observatory/row-journey/orders/row-7",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        },
        receive,
    )
    spec = OperationSpec(
        operation_name="row_journey",
        surface="http",
        action="dataset.read",
        resource_type="dataset",
        resource_keys=("table_id", "row_id"),
    )

    resource = asyncio.run(
        resolve_resource(request, spec, {"table_id": "orders", "row_id": "row-7"})
    )

    assert resource.resource_id == "table_id=orders|row_id=row-7"


def test_unclassified_route_fails_mechanical_validation() -> None:
    candidate = FastAPI()

    @candidate.get("/unclassified")
    def unclassified() -> dict[str, bool]:
        return {"ok": True}

    with pytest.raises(RuntimeError, match="unclassified"):
        validate_manifest(candidate)


def test_manifest_supports_fastapi_lazy_included_routers(monkeypatch) -> None:
    from phlo_api import security_manifest

    router = APIRouter()

    @router.get("/runs/{run_id}", name="included_run")
    def included_run(run_id: str) -> dict[str, str]:
        return {"run_id": run_id}

    included = SimpleNamespace(
        original_router=router,
        include_context=SimpleNamespace(prefix="/api"),
    )
    candidate = SimpleNamespace(routes=[included])
    spec = OperationSpec(
        operation_name="included_run",
        surface="http",
        action="run.read",
        resource_type="run",
    )
    original_manifest = security_manifest.HTTP_ROUTE_MANIFEST
    monkeypatch.setattr(security_manifest, "HTTP_ROUTE_MANIFEST", {"included_run": spec})
    try:
        resolved = validate_manifest(candidate)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/runs/run-1",
                "headers": [],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
            }
        )

        assert {(method, route.path) for route in resolved for method in route.methods} == {
            ("GET", "/api/runs/{run_id}")
        }
        assert _route_for_request(candidate, request) == (
            resolved[0],
            {"run_id": "run-1"},
        )
    finally:
        monkeypatch.setattr(security_manifest, "HTTP_ROUTE_MANIFEST", original_manifest)
        validate_manifest(app)


def test_anonymous_protected_request_is_401_before_handler(monkeypatch, tmp_path) -> None:
    from phlo_api import main

    monkeypatch.setattr("phlo_api.security_manifest.is_regulated", lambda: True)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    called = False

    def load_config() -> dict[str, str]:
        nonlocal called
        called = True
        return {"name": "should-not-run"}

    monkeypatch.setattr(main, "load_phlo_config", load_config)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: None)

    response = TestClient(app).get("/api/config")

    assert response.status_code == 401
    assert called is False


def test_unregulated_anonymous_non_public_request_reaches_handler(monkeypatch, tmp_path) -> None:
    from phlo_api import main

    monkeypatch.setattr("phlo_api.security_manifest.is_regulated", lambda: False)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: None)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(main, "load_phlo_config", lambda: {"name": "development"})

    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"name": "development"}


def test_detailed_health_summary_is_protected(monkeypatch) -> None:
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: None)

    response = TestClient(app).get("/api/observability/health")

    assert response.status_code == 401


def test_unknown_path_remains_a_safe_404() -> None:
    response = TestClient(app).get("/api/does-not-exist")

    assert response.status_code == 404


def test_authenticated_principal_without_permission_is_403(monkeypatch, tmp_path) -> None:
    from phlo_api import main

    auth, canonical = _principal()
    backend = _Backend(allowed=False)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)
    monkeypatch.setattr(main, "load_phlo_config", lambda: {"name": "should-not-run"})

    response = TestClient(app).get("/api/config", headers={"Authorization": "Bearer viewer"})

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "reason": "access_denied"}
    assert backend.calls and backend.calls[0][1] == "platform_metadata.read"


def test_scoped_principal_is_allowed_and_resource_is_checked(monkeypatch, tmp_path) -> None:
    from phlo_api import main

    auth, canonical = _principal("operator")
    backend = _Backend(allowed=True)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    (tmp_path / "phlo.yaml").write_text("name: allowed\n")
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)
    monkeypatch.setattr(main, "load_phlo_config", lambda: {"name": "allowed"})

    response = TestClient(app).get("/api/config", headers={"Authorization": "Bearer operator"})

    assert response.status_code == 200
    assert response.json() == {"name": "allowed"}
    assert backend.calls[0] == ("operator", "platform_metadata.read", "project")


def test_authorization_receives_request_correlation_before_handler(monkeypatch, tmp_path) -> None:
    auth, canonical = _principal("operator")
    backend = _Backend(allowed=True)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    (tmp_path / "phlo.yaml").write_text("name: allowed\n")
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)

    response = TestClient(app).get(
        "/api/config",
        headers={"Authorization": "Bearer operator", "X-Request-Id": "corr-123"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "corr-123"
    assert backend.contexts[0].request_id == "corr-123"


def test_authenticated_request_without_backend_is_503(monkeypatch) -> None:
    auth, canonical = _principal("operator")
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: None)

    response = TestClient(app).get("/api/config", headers={"Authorization": "Bearer operator"})

    assert response.status_code == 503
    assert response.json() == {
        "error": "service_unavailable",
        "reason": "authorization_unavailable",
    }


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("allow", 200), ("deny", 403), ("canonicalization_failed", 503)],
)
def test_regulated_route_uses_auth_principal_and_actual_policy_target(
    monkeypatch, tmp_path, decision: str, expected_status: int
) -> None:
    from phlo_api import main

    auth, _canonical = _principal("operator")
    calls: list[tuple[object, str, str, str]] = []

    def enforce_call(*, principal, action, resource, **_kwargs):  # noqa: ANN001
        calls.append((principal, action, resource.resource_type, resource.resource_id))
        assert isinstance(principal, AuthPrincipal)
        if decision == "allow":
            return EnforcementResult.allow()
        if decision == "deny":
            return EnforcementResult.deny(reason_code="default_deny")
        return EnforcementResult.error(reason_code="canonicalization_failed")

    monkeypatch.setattr("phlo_api.security_manifest.is_regulated", lambda: True)
    monkeypatch.setattr("phlo_api.security_manifest.enforce", enforce_call)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda *_args, **_kwargs: pytest.fail("regulated path must pass AuthPrincipal to core"),
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(main, "load_phlo_config", lambda: {"name": "regulated"})

    response = TestClient(app).get("/api/config", headers={"Authorization": "Bearer operator"})

    assert response.status_code == expected_status
    assert calls == [(auth, "platform_metadata.read", "platform_metadata", "project")]
    if expected_status == 200:
        assert response.json() == {"name": "regulated"}


def test_regulated_anonymous_route_is_401_before_policy(monkeypatch) -> None:
    monkeypatch.setattr("phlo_api.security_manifest.is_regulated", lambda: True)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: None)

    response = TestClient(app).get("/api/config")

    assert response.status_code == 401


def test_composite_body_resources_are_authorized_as_one_deterministic_identity(monkeypatch) -> None:
    auth, canonical = _principal("analyst")
    backend = _Backend(allowed=False)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)

    response = TestClient(app).post(
        "/api/observatory/query",
        json={
            "dataset_id": "orders",
            "table_name": "payments",
            "sql": "select * from payments",
        },
        headers={"Authorization": "Bearer analyst"},
    )

    assert response.status_code == 403
    assert backend.calls == [("analyst", "dataset.query", "project")]


def test_query_ignores_synthetic_dataset_id_and_denies_before_handler(monkeypatch) -> None:
    auth, canonical = _principal("analyst")
    backend = _Backend(allowed=False)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._run_read_query",
        lambda _request: pytest.fail("query handler must not run after policy denial"),
    )

    response = TestClient(app).post(
        "/api/observatory/query",
        json={"dataset_id": "orders", "sql": "select * from payments"},
        headers={"Authorization": "Bearer analyst"},
    )

    assert response.status_code == 403
    assert backend.calls == [("analyst", "dataset.query", "project")]


def test_multi_table_query_is_rejected_before_handler(monkeypatch) -> None:
    auth, canonical = _principal("analyst")
    backend = _Backend(allowed=True)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._run_read_query",
        lambda _request: pytest.fail("unsupported SQL must not reach the query handler"),
    )

    response = TestClient(app).post(
        "/api/observatory/query",
        json={"sql": "select * from orders join payments on true"},
        headers={"Authorization": "Bearer analyst"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "unsupported_query"}
    assert backend.calls == []


def test_branch_action_binds_action_id_branch_before_handler(monkeypatch) -> None:
    auth, canonical = _principal("catalog-operator")
    backend = _Backend(allowed=False)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._execute_branch_action",
        lambda _request: pytest.fail("branch handler must not run after policy denial"),
    )

    response = TestClient(app).post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:delete:payments"},
        headers={"Authorization": "Bearer catalog-operator"},
    )

    assert response.status_code == 403
    assert backend.calls == [("catalog-operator", "catalog.manage", "branch_name=payments")]


def test_branch_action_conflicting_body_identity_is_rejected(monkeypatch) -> None:
    auth, canonical = _principal("catalog-operator")
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )

    response = TestClient(app).post(
        "/api/observatory/branches/actions",
        json={"action_id": "branch:delete:payments", "branch_name": "orders"},
        headers={"Authorization": "Bearer catalog-operator"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "ambiguous_resource"}


def test_dataset_publish_requires_publish_action_and_dataset_identity(monkeypatch) -> None:
    auth, canonical = _principal("dataset-editor")
    backend = _Backend(allowed=False)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)
    monkeypatch.setattr(
        "phlo_api.observatory_api.observatory._execute_dataset_workflow_action",
        lambda _request: pytest.fail("dataset mutation must not run after policy denial"),
    )

    response = TestClient(app).post(
        "/api/observatory/actions",
        json={"action_id": "dataset:orders:publish"},
        headers={"Authorization": "Bearer dataset-editor"},
    )

    assert response.status_code == 403
    assert backend.calls == [("dataset-editor", "dataset.publish", "dataset_id=orders")]


@pytest.mark.parametrize(
    ("path", "action", "resource_type"),
    [
        ("/api/observatory/runs?run_id=allowed", "run.read", "run"),
        ("/api/observatory/branches?branch_name=allowed", "catalog.read", "catalog"),
        ("/api/observatory/asset-graph?asset_key=allowed", "asset.read", "asset"),
        ("/api/observatory/saved-queries?dataset_id=allowed", "dataset.read", "dataset"),
        ("/api/services?name=allowed", "service.read", "service"),
        ("/api/config?name=allowed", "platform_metadata.read", "platform_metadata"),
    ],
)
def test_collection_query_keys_cannot_shrink_authorization_scope(
    monkeypatch, path: str, action: str, resource_type: str
) -> None:
    auth, canonical = _principal("viewer")
    backend = _Backend(allowed=False)
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        "phlo_api.security_manifest.resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr("phlo_api.security_manifest.get_authorization_backend", lambda: backend)

    response = TestClient(app).get(path, headers={"Authorization": "Bearer viewer"})

    assert response.status_code == 403
    assert backend.calls == [("viewer", action, "project")]


def test_path_body_identity_mismatch_is_rejected_before_authorization() -> None:
    from phlo_api.security_manifest import OperationSpec, resolve_resource

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps({"dataset_id": "payments"}).encode(),
            "more_body": False,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/datasets/orders",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"25"),
            ],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        },
        receive,
    )
    spec = OperationSpec(
        operation_name="test",
        surface="http",
        action="dataset.read",
        resource_type="dataset",
        resource_keys=("dataset_id",),
        resource_sources=(("dataset_id", "path_body"),),
    )

    with pytest.raises(Exception, match="ambiguous_resource"):
        asyncio.run(resolve_resource(request, spec, {"dataset_id": "orders"}))


def test_chunked_body_query_conflict_is_rejected_before_handler(monkeypatch) -> None:
    auth, _canonical = _principal("chunked-user")
    monkeypatch.setattr("phlo_api.security_manifest.get_request_principal", lambda _request: auth)
    called = False
    route = next(
        route
        for route in app.routes
        if getattr(route, "name", None) == "post_observatory_schema_diff"
    )
    original_endpoint = route.endpoint

    async def endpoint_not_called(*_args, **_kwargs):  # noqa: ANN002, ANN003
        nonlocal called
        called = True
        return {"unexpected": True}

    route.endpoint = endpoint_not_called

    messages: list[dict[str, object]] = []

    async def receive():
        if not hasattr(receive, "sent"):
            receive.sent = 1
            return {
                "type": "http.request",
                "body": b'{"asset_key":"',
                "more_body": True,
            }
        return {
            "type": "http.request",
            "body": b"target" + b'"}',
            "more_body": False,
        }

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/observatory/schemas/diff",
        "raw_path": b"/api/observatory/schemas/diff",
        "query_string": b"asset_key=allowed",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    }
    try:
        asyncio.run(app(scope, receive, send))
    finally:
        route.endpoint = original_endpoint

    assert messages[0]["status"] == 400
    assert called is False
