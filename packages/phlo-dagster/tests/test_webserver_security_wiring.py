"""Tests for the shipped Dagster webserver enforcement boundary.

Covers classification of the installed GraphQL schema, HTTP authentication
wrapping of the webserver ASGI app, GraphQL-over-WebSocket authentication, and
the authorization middleware applied in front of them.
"""

from pathlib import Path
import yaml
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from dagster_graphql.schema import create_schema
from dagster_webserver.webserver import DagsterWebserver
from phlo.capabilities import AuthPrincipal
from phlo.security.adapters import EnforcementResult
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from phlo_dagster.authorization import (
    extract_dagster_run_id_from_log_key,
    extract_dagster_run_id_from_log_path,
    resolve_graphql_operation,
    validate_graphql_schema,
)
import phlo_dagster.authorization as authorization
from phlo_dagster.authorization_middleware import DagsterGraphQLAuthorizationMiddleware
from phlo_dagster.webserver import (
    DagsterHTTPAuthenticationASGI,
    GRAPHQL_WS_INIT_TIMEOUT_ENV,
    GraphQLWebSocketAuthenticationASGI,
    PhloDagsterWebserver,
    build_dagster_http_manifest,
    build_durable_nonce_store,
)
from _oidc_test_helpers import (
    AUDIENCE,
    ISSUER,
    JWKS_URL,
    JWKSResponse,
    key_and_jwks,
    token,
)


def _websocket(
    headers: dict[str, str],
    *,
    connection_init: dict[str, object] | None = None,
    host: str = "127.0.0.1",
) -> SimpleNamespace:
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=host),
        path="/graphql",
        scope={"phlo_graphql_connection_init": connection_init}
        if connection_init is not None
        else {},
    )


def test_installed_dagster_schema_is_fully_classified() -> None:
    validate_graphql_schema(create_schema().graphql_schema)


@pytest.mark.parametrize(
    ("operation", "field", "root_name"),
    [
        ("query", "componentsForLocationOrError", "Query"),
        ("mutation", "refreshComponentState", "Mutation"),
    ],
)
def test_schema_registry_accepts_optional_component_fields_when_present(
    monkeypatch,
    operation,
    field,
    root_name,
) -> None:
    query_fields = {
        registered_field: SimpleNamespace()
        for registered_operation, registered_field in authorization._GRAPHQL_OPERATION_INDEX
        if registered_operation == operation
    }
    query_fields[field] = SimpleNamespace()
    schema = SimpleNamespace(
        get_type=lambda name: SimpleNamespace(fields=query_fields) if name == root_name else None
    )
    monkeypatch.setattr(authorization, "validate_graphql_resource_bindings", lambda _schema: None)

    validate_graphql_schema(schema)


def test_development_webserver_omits_graphql_authorization_middleware(monkeypatch) -> None:
    server = object.__new__(PhloDagsterWebserver)
    server._graphene_schema = create_schema()
    monkeypatch.setenv("PHLO_REGULATED", "false")

    assert server.build_graphql_middleware() == []


def test_regulated_webserver_discovers_project_capabilities_before_authorization(
    monkeypatch,
) -> None:
    server = object.__new__(PhloDagsterWebserver)
    server._graphene_schema = create_schema()
    monkeypatch.setenv("PHLO_REGULATED", "true")
    discovered = False

    def discover() -> None:
        nonlocal discovered
        discovered = True

    monkeypatch.setattr("phlo_dagster.webserver.discover_capabilities", discover)

    middleware = server.build_graphql_middleware()

    assert discovered
    assert len(middleware) == 1
    assert isinstance(middleware[0], DagsterGraphQLAuthorizationMiddleware)


def test_service_entrypoint_uses_secured_webserver_module() -> None:
    service_yaml = Path(__file__).parents[1] / "src" / "phlo_dagster" / "service.yaml"
    definition = yaml.safe_load(service_yaml.read_text())
    command = definition["compose"]["command"]

    module_index = command.index("-m")
    assert command[module_index + 1] == "phlo_dagster.webserver"


def test_inherited_dagster_http_routes_are_classified_by_method_and_path() -> None:
    server = object.__new__(PhloDagsterWebserver)
    server._app_path_prefix = ""
    routes = server.build_routes()

    manifest = build_dagster_http_manifest(routes)

    assert ("GET", "/server_info") in manifest
    assert manifest[("GET", "/server_info")].public
    assert manifest[("GET", "/download_debug/{run_id:str}")].resource_keys == ("run_id",)
    assert manifest[("POST", "/report_asset_check/{asset_key:path}")].action == "asset.execute"
    assert all(spec.action and spec.resource_type for spec in manifest.values())


def test_inherited_websocket_routes_are_classified_or_rejected() -> None:
    async def graphql(_scope, _receive, _send):  # noqa: ANN001
        return None

    async def future(_scope, _receive, _send):  # noqa: ANN001
        return None

    manifest = build_dagster_http_manifest([WebSocketRoute("/graphql", graphql)])
    assert ("WEBSOCKET", "/graphql") in manifest

    with pytest.raises(RuntimeError, match="Unclassified Dagster WebSocket route"):
        build_dagster_http_manifest([WebSocketRoute("/future", future)])


def test_server_info_is_minimal_public_readiness() -> None:
    server = object.__new__(PhloDagsterWebserver)

    response = asyncio.run(server.webserver_info_endpoint(None))

    assert response.status_code == 200
    assert response.body == b'{"status":"healthy"}'


@pytest.mark.parametrize(
    ("principal", "decision", "status_code"),
    [
        (None, None, 401),
        (AuthPrincipal(subject="viewer", principal_type="user"), "deny", 403),
        (AuthPrincipal(subject="operator", principal_type="user"), "allow", 200),
    ],
)
def test_inherited_http_route_enforces_before_handler(
    monkeypatch, principal, decision: str | None, status_code: int
) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    called = False

    async def endpoint(_request):  # noqa: ANN001
        nonlocal called
        called = True
        return JSONResponse({"ok": True})

    downstream = Starlette(
        routes=[
            Route(
                "/download_debug/{run_id:str}",
                endpoint,
                name="download_debug_file_endpoint",
            )
        ]
    )
    middleware = MagicMock()
    middleware._extract_principal.return_value = principal
    secured = DagsterHTTPAuthenticationASGI(downstream, middleware, downstream.routes)
    if decision == "allow":
        monkeypatch.setattr("phlo.security.enforce", lambda **_kwargs: EnforcementResult.allow())
    elif decision == "deny":
        monkeypatch.setattr(
            "phlo.security.enforce",
            lambda **_kwargs: EnforcementResult.deny(reason_code="default_deny"),
        )

    with TestClient(secured) as client:
        response = client.get("/download_debug/run-42")

    assert response.status_code == status_code
    assert called is (status_code == 200)


def test_regulated_graphql_http_boundary_preserves_validated_principal_for_graphql_execution(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    principal = AuthPrincipal(subject="service:phlo-api", principal_type="service")
    middleware = MagicMock()
    middleware._extract_principal.return_value = principal

    async def endpoint(request):  # noqa: ANN001
        propagated = request.scope.get("phlo_authenticated_principal")
        return JSONResponse({"subject": propagated.subject if propagated else None})

    downstream = Starlette(routes=[Route("/graphql", endpoint, methods=["POST"])])
    secured = DagsterHTTPAuthenticationASGI(downstream, middleware, downstream.routes)

    with TestClient(secured) as client:
        response = client.post("/graphql")

    assert response.status_code == 200
    assert response.json() == {"subject": "service:phlo-api"}


def test_development_graphql_http_allows_unauthenticated_execution(monkeypatch) -> None:
    called = False

    async def endpoint(_request):  # noqa: ANN001
        nonlocal called
        called = True
        return JSONResponse({"ok": True})

    monkeypatch.setenv("PHLO_REGULATED", "false")
    downstream = Starlette(routes=[Route("/graphql", endpoint, methods=["POST"])])
    middleware = MagicMock()
    middleware._extract_principal.return_value = None
    secured = DagsterHTTPAuthenticationASGI(downstream, middleware, downstream.routes)

    with TestClient(secured) as client:
        response = client.post("/graphql")

    assert response.status_code == 200
    assert called
    middleware._extract_principal.assert_not_called()


def test_regulated_graphql_http_requires_authentication(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    downstream = Starlette(
        routes=[Route("/graphql", lambda _request: JSONResponse({"ok": True}), methods=["POST"])]
    )
    middleware = MagicMock()
    middleware._extract_principal.return_value = None
    secured = DagsterHTTPAuthenticationASGI(downstream, middleware, downstream.routes)

    with TestClient(secured) as client:
        response = client.post("/graphql")

    assert response.status_code == 401


def test_dagster_log_keys_bind_http_authorization_to_the_run_before_handler(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    assert (
        extract_dagster_run_id_from_log_key(["run-allowed", "compute_logs", "step"])
        == "run-allowed"
    )
    assert (
        extract_dagster_run_id_from_log_path("run-allowed/compute_logs/step/stdout")
        == "run-allowed"
    )
    assert extract_dagster_run_id_from_log_key(["run-allowed", "events", "step"]) is None
    assert extract_dagster_run_id_from_log_path("run-allowed/not-compute-logs/step/stdout") is None

    calls: list[str] = []
    called_runs: list[str] = []

    async def endpoint(request):  # noqa: ANN001
        called_runs.append(request.path_params["path"])
        return JSONResponse({"ok": True})

    downstream = Starlette(routes=[Route("/logs/{path:path}", endpoint, name="dagster_logs")])
    middleware = MagicMock()
    middleware._extract_principal.return_value = AuthPrincipal(
        subject="viewer", principal_type="user"
    )

    def enforce(**kwargs):  # noqa: ANN003
        calls.append(kwargs["resource"].resource_id)
        if kwargs["resource"].resource_id == "runId=run-allowed":
            return EnforcementResult.allow()
        return EnforcementResult.deny(reason_code="default_deny")

    monkeypatch.setattr("phlo.security.enforce", enforce)
    secured = DagsterHTTPAuthenticationASGI(downstream, middleware, downstream.routes)

    with TestClient(secured) as client:
        allowed = client.get("/logs/run-allowed/compute_logs/step/stdout")
        denied = client.get("/logs/run-other/compute_logs/step/stdout")
        malformed = client.get("/logs/run-allowed/not-compute-logs/step/stdout")

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert malformed.status_code == 403
    assert calls == ["runId=run-allowed", "runId=run-other"]
    assert called_runs == ["run-allowed/compute_logs/step/stdout"]


def test_dagster_graphql_captured_logs_bind_log_key_to_one_run() -> None:
    middleware = DagsterGraphQLAuthorizationMiddleware.__new__(
        DagsterGraphQLAuthorizationMiddleware
    )
    spec = resolve_graphql_operation("query", "capturedLogs")

    assert middleware._graphql_resource_ids(
        spec,
        "capturedLogs",
        {"logKey": ["run-allowed", "compute_logs", "step"]},
    ) == ["runId=run-allowed"]
    with pytest.raises(Exception, match="Malformed or ambiguous"):
        middleware._graphql_resource_ids(
            spec,
            "capturedLogs",
            {"logKey": ["run-other", "events", "step"]},
        )


def test_ordinary_generated_dagster_service_keeps_oidc_optional(monkeypatch) -> None:
    service_yaml = Path(__file__).parents[1] / "src" / "phlo_dagster" / "service.yaml"
    definition = yaml.safe_load(service_yaml.read_text())

    monkeypatch.delenv("PHLO_DAGSTER_OIDC_REQUIRED", raising=False)
    assert not PhloDagsterWebserver._oidc_required()
    assert definition["compose"]["environment"]["PHLO_DAGSTER_OIDC_REQUIRED"] == (
        "${PHLO_DAGSTER_OIDC_REQUIRED:-false}"
    )


def test_regulated_dagster_startup_fails_without_complete_oidc(monkeypatch) -> None:
    for name in (
        "PHLO_DAGSTER_OIDC_ISSUER",
        "PHLO_DAGSTER_OIDC_AUDIENCE",
        "PHLO_DAGSTER_OIDC_JWKS_URL",
        "PHLO_DAGSTER_OIDC_CA_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_REQUIRED", "true")
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setattr("phlo_dagster.webserver.discover_capabilities", lambda: None)
    server = object.__new__(PhloDagsterWebserver)
    server._graphene_schema = create_schema()

    with pytest.raises(RuntimeError, match="OIDC identity is required"):
        server.build_graphql_middleware()


def test_server_info_route_binds_to_phlo_readiness_override(monkeypatch) -> None:
    server = object.__new__(PhloDagsterWebserver)
    server._app_path_prefix = None
    monkeypatch.setattr(server, "build_static_routes", lambda: [])

    routes = server.build_routes()
    server_info = next(route for route in routes if route.path == "/server_info")

    assert server_info.endpoint.__self__ is server
    assert server_info.endpoint.__func__ is PhloDagsterWebserver.webserver_info_endpoint


def test_dagster_cli_factory_instantiates_phlo_webserver(monkeypatch) -> None:
    import dagster_webserver.app as dagster_app

    marker = object()
    monkeypatch.setattr(dagster_app.check, "inst_param", lambda *args, **kwargs: None)
    monkeypatch.setattr(dagster_app, "warn_if_compute_logs_disabled", lambda: None)
    monkeypatch.setattr(dagster_app, "log_workspace_stats", lambda *args, **kwargs: None)
    monkeypatch.setattr(PhloDagsterWebserver, "create_asgi_app", lambda self, **kwargs: marker)

    result = dagster_app.create_app_from_workspace_process_context(
        SimpleNamespace(instance=SimpleNamespace())
    )

    assert dagster_app.DagsterWebserver is PhloDagsterWebserver
    assert result is marker


def test_create_asgi_app_installs_inherited_http_and_graphql_wrappers(monkeypatch) -> None:
    server = object.__new__(PhloDagsterWebserver)
    downstream = Starlette(
        routes=[
            Route(
                "/server_info",
                lambda _request: JSONResponse({"status": "healthy"}),
                name="server_info",
            ),
        ]
    )
    middleware = MagicMock()
    monkeypatch.setattr(
        DagsterWebserver,
        "create_asgi_app",
        lambda *_args, **_kwargs: downstream,
    )
    monkeypatch.setattr(server, "_get_graphql_authorization_middleware", lambda: middleware)

    secured = server.create_asgi_app()

    assert isinstance(secured, DagsterHTTPAuthenticationASGI)
    assert isinstance(secured.app, GraphQLWebSocketAuthenticationASGI)
    assert secured.app.app is downstream


def test_graphql_ws_requires_authenticated_human(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.delenv("PHLO_DAGSTER_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("PHLO_DAGSTER_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("PHLO_DAGSTER_OIDC_JWKS_URL", raising=False)
    server = object.__new__(PhloDagsterWebserver)

    result = asyncio.run(
        server.execute_graphql_subscription(
            _websocket({}),
            "1",
            "subscription Logs { pipelineRunLogs { __typename } }",
            None,
            "Logs",
        )
    )

    assert result[0] is None
    assert result[1]["extensions"]["code"] == "UNAUTHENTICATED"


def test_graphql_ws_verified_oidc_human_can_pass_or_is_denied_by_policy(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    private_key, jwks = key_and_jwks()
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    server = object.__new__(PhloDagsterWebserver)
    accepted = MagicMock(allowed=True, reason_code=None)

    async def fake_super(*_args, **_kwargs):
        return "started", None

    with patch("phlo_dagster.authorization_middleware.enforce", return_value=accepted):
        with patch.object(
            DagsterWebserver,
            "execute_graphql_subscription",
            new=fake_super,
        ):
            result = asyncio.run(
                server.execute_graphql_subscription(
                    _websocket({"X-Auth-Request-Access-Token": token(private_key)}),
                    "1",
                    "subscription Logs { pipelineRunLogs { __typename } }",
                    None,
                    "Logs",
                )
            )
    assert result == ("started", None)

    with patch(
        "phlo_dagster.authorization_middleware.enforce",
        return_value=MagicMock(allowed=False, reason_code="default_deny"),
    ):
        result = asyncio.run(
            server.execute_graphql_subscription(
                _websocket({"X-Auth-Request-Access-Token": token(private_key)}),
                "1",
                "subscription Logs { pipelineRunLogs { __typename } }",
                None,
                "Logs",
            )
        )
    assert result[0] is None
    assert result[1]["extensions"]["code"] == "FORBIDDEN"


def test_graphql_ws_connection_init_requires_access_token_field(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    private_key, jwks = key_and_jwks()
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    server = object.__new__(PhloDagsterWebserver)

    result = asyncio.run(
        server.execute_graphql_subscription(
            _websocket(
                {"X-Auth-Request-User": "forged"},
                connection_init={"token": token(private_key)},
            ),
            "1",
            "subscription Logs { pipelineRunLogs { __typename } }",
            None,
            "Logs",
        )
    )

    assert result[0] is None
    assert result[1]["extensions"]["code"] == "UNAUTHENTICATED"


def test_graphql_ws_asgi_protocol_authenticates_connection_init(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    private_key, jwks = key_and_jwks()
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setattr(
        "phlo_dagster.oidc_identity.httpx.stream",
        lambda *_args, **_kwargs: JWKSResponse(jwks),
    )
    middleware = DagsterGraphQLAuthorizationMiddleware()

    async def endpoint(websocket):  # noqa: ANN001
        await websocket.receive()
        await websocket.accept(subprotocol="graphql-ws")
        init = await websocket.receive_json()
        assert init["type"] == "connection_init"
        await websocket.send_json({"type": "connection_ack"})
        await websocket.close()

    downstream = Starlette(routes=[WebSocketRoute("/graphql", endpoint)])
    asgi = GraphQLWebSocketAuthenticationASGI(downstream, middleware)

    with TestClient(asgi) as client:
        with client.websocket_connect("/graphql", subprotocols=["graphql-ws"]) as websocket:
            websocket.send_json(
                {"type": "connection_init", "payload": {"access_token": token(private_key)}}
            )
            assert websocket.accepted_subprotocol == "graphql-ws"
            assert websocket.receive_json() == {"type": "connection_ack"}


def _websocket_downstream():
    async def endpoint(websocket):  # noqa: ANN001
        await websocket.receive()
        await websocket.accept(subprotocol="graphql-ws")
        await websocket.receive_json()
        await websocket.send_json({"type": "connection_ack"})
        await websocket.close()

    return Starlette(routes=[WebSocketRoute("/graphql", endpoint)])


@pytest.mark.parametrize("offered", [None, ["graphql-transport-ws"]])
def test_graphql_ws_rejects_missing_or_unsupported_subprotocol(monkeypatch, offered) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    asgi = GraphQLWebSocketAuthenticationASGI(
        _websocket_downstream(), DagsterGraphQLAuthorizationMiddleware()
    )

    with TestClient(asgi) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            if offered is None:
                with client.websocket_connect("/graphql"):
                    pass
            else:
                with client.websocket_connect("/graphql", subprotocols=offered):
                    pass

    assert error.value.code == 4406


def test_graphql_ws_invalid_connection_init_token_closes_unauthenticated(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    asgi = GraphQLWebSocketAuthenticationASGI(
        _websocket_downstream(), DagsterGraphQLAuthorizationMiddleware()
    )

    with TestClient(asgi) as client:
        with client.websocket_connect("/graphql", subprotocols=["graphql-ws"]) as websocket:
            websocket.send_json({"type": "connection_init", "payload": {"access_token": "invalid"}})
            error = websocket.receive()

    assert error == {"type": "websocket.close", "code": 4401}


def test_graphql_ws_idle_connection_init_times_out(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setenv(GRAPHQL_WS_INIT_TIMEOUT_ENV, "0.05")
    asgi = GraphQLWebSocketAuthenticationASGI(
        _websocket_downstream(), DagsterGraphQLAuthorizationMiddleware()
    )

    with TestClient(asgi) as client:
        with client.websocket_connect("/graphql", subprotocols=["graphql-ws"]) as websocket:
            error = websocket.receive()

    assert error == {"type": "websocket.close", "code": 4408}


def test_graphql_ws_timeout_configuration_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv(GRAPHQL_WS_INIT_TIMEOUT_ENV, "61")

    with pytest.raises(ValueError, match=GRAPHQL_WS_INIT_TIMEOUT_ENV):
        GraphQLWebSocketAuthenticationASGI(
            _websocket_downstream(), DagsterGraphQLAuthorizationMiddleware()
        )


def test_server_info_readiness_tracks_expired_jwks_refresh(monkeypatch) -> None:
    private_key, jwks = key_and_jwks()
    del private_key
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("PHLO_DAGSTER_OIDC_REQUIRED", "true")
    responses = [JWKSResponse(jwks)]

    def stream(*_args, **_kwargs):  # noqa: ANN001, ANN202
        if responses:
            return responses.pop(0)
        raise httpx.HTTPError("JWKS unavailable")

    monkeypatch.setattr("phlo_dagster.oidc_identity.httpx.stream", stream)
    server = object.__new__(PhloDagsterWebserver)
    middleware = server._get_graphql_authorization_middleware()

    healthy = asyncio.run(server.webserver_info_endpoint(None))
    middleware._oidc_validator._keys_fetched_at = (
        time.monotonic() - middleware._oidc_validator.cache_ttl - 1
    )
    unhealthy = asyncio.run(server.webserver_info_endpoint(None))

    assert healthy.status_code == 200
    assert unhealthy.status_code == 503
    assert unhealthy.body == b'{"status":"unhealthy","reason":"oidc_unready"}'


class _DurableNonceStore:
    """Replay store used by webserver wiring tests; records schema init."""

    def __init__(self) -> None:
        self.consumed: set[str] = set()
        self.schema_initialized = False

    def consume(self, nonce: str, *, expires_at) -> bool:
        del expires_at
        if nonce in self.consumed:
            return False
        self.consumed.add(nonce)
        return True

    def ensure_schema(self) -> None:
        self.schema_initialized = True


def _write_workload_credentials(tmp_path, secret: str) -> None:
    import json

    path = tmp_path / "workload-credentials.json"
    path.write_text(
        json.dumps(
            {
                "phlo-api": {
                    "phlo-dagster": {
                        "scp": ["dagster:control"],
                        "keys": {"k1": {"secret": secret, "state": "active", "activated_at": 0}},
                    }
                }
            }
        )
    )
    path.chmod(0o600)
    return str(path)


def test_regulated_webserver_wires_durable_nonce_store_and_initializes_schema(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "true")
    store = _DurableNonceStore()
    monkeypatch.setattr("phlo_dagster.webserver.build_durable_nonce_store", lambda: store)

    server = object.__new__(PhloDagsterWebserver)
    middleware = server._get_graphql_authorization_middleware()

    assert middleware._nonce_store is store
    assert store.schema_initialized  # receiver-owned lifecycle ran ensure_schema
    assert server._get_graphql_authorization_middleware() is middleware
    assert store.schema_initialized


def test_development_webserver_builds_no_durable_nonce_store(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_REGULATED", "false")
    store = _DurableNonceStore()
    monkeypatch.setattr("phlo_dagster.webserver.build_durable_nonce_store", lambda: store)

    server = object.__new__(PhloDagsterWebserver)
    middleware = server._get_graphql_authorization_middleware()

    assert middleware._nonce_store is None
    assert not store.schema_initialized


def test_build_durable_nonce_store_returns_postgres_nonce_store(monkeypatch) -> None:
    from phlo.security.service_identity import PostgresNonceStore

    monkeypatch.setenv("PHLO_SERVICE_NONCE_DB_URL", "postgresql://u:p@h:5432/db")
    pool = object()
    monkeypatch.setattr("psycopg2.pool.ThreadedConnectionPool", lambda *a, **k: pool)

    store = build_durable_nonce_store()

    assert isinstance(store, PostgresNonceStore)
    assert store._connection_or_pool is pool


def test_build_durable_nonce_store_returns_none_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_SERVICE_NONCE_DB_URL", raising=False)
    monkeypatch.delenv("PHLO_RUN_EVIDENCE_DB_URL", raising=False)
    assert build_durable_nonce_store() is None


def test_regulated_graphql_http_accepts_scoped_phlo1_token_through_webserver_wiring(
    monkeypatch, tmp_path
) -> None:
    from phlo.security.service_identity import (
        create_scoped_service_token,
        load_service_identity_credentials,
    )

    secret = "wls-secret"
    _write_workload_credentials(tmp_path, secret)
    monkeypatch.setenv("PHLO_SERVICE_CREDENTIALS_FILE", str(tmp_path / "workload-credentials.json"))
    monkeypatch.setenv("PHLO_REGULATED", "true")
    store = _DurableNonceStore()
    monkeypatch.setattr("phlo_dagster.webserver.build_durable_nonce_store", lambda: store)

    token_text = create_scoped_service_token(
        "phlo-api",
        audience="phlo-dagster",
        scp=("dagster:control",),
        credentials=load_service_identity_credentials(),
    )

    async def endpoint(request):  # noqa: ANN001
        principal = request.scope.get("phlo_authenticated_principal")
        return JSONResponse(
            {
                "subject": principal.subject if principal else None,
                "type": principal.principal_type if principal else None,
            }
        )

    downstream = Starlette(routes=[Route("/graphql", endpoint, methods=["POST"])])
    monkeypatch.setattr(DagsterWebserver, "create_asgi_app", lambda *_a, **_k: downstream)

    server = object.__new__(PhloDagsterWebserver)
    secured = server.create_asgi_app()

    # The middleware handed to the ASGI boundary carries the durable store.
    assert server._get_graphql_authorization_middleware()._nonce_store is store
    assert store.schema_initialized

    with TestClient(secured) as client:
        accepted = client.post("/graphql", headers={"Authorization": f"Bearer {token_text}"})
        # The same token cannot be replayed once its nonce is consumed.
        replayed = client.post("/graphql", headers={"Authorization": f"Bearer {token_text}"})

    assert accepted.status_code == 200
    assert accepted.json() == {"subject": "service:phlo-api", "type": "service"}
    assert replayed.status_code == 401
