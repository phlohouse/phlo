"""Phlo's authenticated Dagster webserver entrypoint.

Every inherited Dagster HTTP route must be classified at startup; an
unclassified route fails startup rather than bypassing auth. GraphQL passes a
mandatory authorization middleware and graphql-ws connections authenticate at
connection_init; the secured class is patched into dagster_webserver.app.
Runnable process entrypoint rather than a library import; discovers capabilities
through phlo.capabilities.discovery and enforces phlo.rbac.models/phlo.security policy.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable
from uuid import uuid4

from dagster_webserver import app as dagster_app
from dagster_webserver.cli import dagster_webserver
from dagster_webserver.graphql import GraphQLWS
from dagster_webserver.webserver import DagsterWebserver
from graphql import GraphQLError, parse
from starlette.responses import JSONResponse
from starlette.routing import Match

from phlo.capabilities.discovery import discover_capabilities
from phlo_dagster.authorization import (
    extract_dagster_run_id_from_log_path,
    get_adapter,
    validate_graphql_schema,
)
from phlo_dagster.authorization_middleware import DagsterGraphQLAuthorizationMiddleware
from phlo_dagster.oidc_identity import OIDC_REQUIRED_ENV
from phlo.security.mode import requires_http_authorization
from phlo.security.service_identity import PostgresNonceStore

GRAPHQL_WS_INIT_TIMEOUT_ENV = "PHLO_DAGSTER_GRAPHQL_WS_INIT_TIMEOUT_SECONDS"
_DEFAULT_GRAPHQL_WS_INIT_TIMEOUT = 10.0
_MAX_GRAPHQL_WS_INIT_TIMEOUT = 60.0

# The Dagster webserver is the receiver of phlo1 service tokens sent by
# phlo-api. Its replay state lives in a durable PostgreSQL store (ADR 0047)
# so authenticated requests survive across webserver replicas and restarts.
PHLO_SERVICE_NONCE_DB_URL_ENV = "PHLO_SERVICE_NONCE_DB_URL"
_RUN_EVIDENCE_DB_URL_ENV = "PHLO_RUN_EVIDENCE_DB_URL"


def _durable_nonce_store_dsn() -> str | None:
    """Resolve the PostgreSQL DSN backing the durable nonce store.

    Prefer the dedicated nonce-store DSN; fall back to the run-evidence DSN,
    which already points at the shared `phlo` database the receiver owns.
    """
    return os.environ.get(PHLO_SERVICE_NONCE_DB_URL_ENV) or os.environ.get(_RUN_EVIDENCE_DB_URL_ENV)


def build_durable_nonce_store() -> PostgresNonceStore | None:
    """Build a durable nonce store backed by the receiver's PostgreSQL pool.

    Returns None when no DSN is configured so regulated receivers without a
    database continue to fail closed rather than accepting tokens without a
    replay guard. Construction does not create a global connection; the caller
    (the webserver receiver) owns this store and its schema lifecycle.
    """
    dsn = _durable_nonce_store_dsn()
    if not dsn:
        return None
    from psycopg2.pool import ThreadedConnectionPool

    return PostgresNonceStore(ThreadedConnectionPool(1, 1, dsn))


@dataclass(frozen=True)
class DagsterHTTPRouteSpec:
    """Canonical classification for one inherited Dagster HTTP entry point."""

    action: str
    resource_type: str
    resource_keys: tuple[str, ...] = ()
    public: bool = False
    delegated_to_graphql: bool = False


def _classify_dagster_http_route(route: Any) -> DagsterHTTPRouteSpec:
    """Classify an actual Dagster route, failing startup on new surfaces."""
    from phlo.rbac.models import CanonicalAction

    name = getattr(route, "name", None)
    path = getattr(route, "path", None)
    if name in {"root_static", "_next_static", "vendor_static"}:
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.PLATFORM_METADATA_READ.value,
            resource_type="platform_metadata",
            public=True,
        )
    if path == "/server_info":
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.OBSERVABILITY_READ.value,
            resource_type="observability",
            public=True,
        )
    if path == "/graphql":
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.ADMIN_READ.value,
            resource_type="admin",
            delegated_to_graphql=True,
        )
    if path == "/download_debug/{run_id:str}":
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.RUN_READ.value,
            resource_type="run",
            resource_keys=("run_id",),
        )
    if path == "/logs/{path:path}":
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.RUN_READ.value,
            resource_type="run",
            resource_keys=("path",),
        )
    if path in {"/notebook", "/dagit/notebook", "/dagit_info"}:
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.ADMIN_READ.value,
            resource_type="admin",
        )
    if path in {
        "/report_asset_materialization/{asset_key:path}",
        "/report_asset_check/{asset_key:path}",
        "/report_asset_observation/{asset_key:path}",
    }:
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.ASSET_EXECUTE.value,
            resource_type="asset",
            resource_keys=("asset_key",),
        )
    if name == "index_html_endpoint":
        return DagsterHTTPRouteSpec(
            action=CanonicalAction.ADMIN_READ.value,
            resource_type="admin",
        )
    raise RuntimeError(f"Unclassified Dagster HTTP route: {name!r} {path!r}")


def build_dagster_http_manifest(routes: list[Any]) -> dict[tuple[str, str], DagsterHTTPRouteSpec]:
    """Build and validate the inherited Dagster HTTP route manifest."""
    manifest: dict[tuple[str, str], DagsterHTTPRouteSpec] = {}
    for route in routes:
        if route.__class__.__name__ == "WebSocketRoute":
            path = getattr(route, "path", None)
            if path != "/graphql":
                raise RuntimeError(f"Unclassified Dagster WebSocket route: {path!r}")
            key = ("WEBSOCKET", path)
            if key in manifest:
                raise RuntimeError(f"Duplicate Dagster WebSocket route: {key!r}")
            manifest[key] = _classify_dagster_http_route(route)
            continue
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not path:
            continue
        spec = _classify_dagster_http_route(route)
        if not methods:
            key = ("MOUNT", path)
            if key in manifest:
                raise RuntimeError(f"Duplicate Dagster HTTP route: {key!r}")
            manifest[key] = spec
            continue
        for method in sorted(methods):
            key = (method, path)
            if key in manifest:
                raise RuntimeError(f"Duplicate Dagster HTTP route: {key!r}")
            manifest[key] = spec
    return manifest


class DagsterHTTPAuthenticationASGI:
    """Authenticate and authorize inherited Dagster HTTP routes."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        middleware: DagsterGraphQLAuthorizationMiddleware,
        routes: list[Any],
    ) -> None:
        self.app = app
        self.middleware = middleware
        self.routes = routes
        self.manifest = build_dagster_http_manifest(routes)

    def _match(self, scope: dict[str, Any]) -> tuple[DagsterHTTPRouteSpec, dict[str, str]] | None:
        method = scope.get("method", "")
        for route in self.routes:
            methods = getattr(route, "methods", None)
            if methods and method not in methods:
                continue
            match, child_scope = route.matches(scope)
            if match is Match.FULL:
                path = getattr(route, "path", "")
                key = (method, path) if methods else ("MOUNT", path)
                spec = self.manifest.get(key)
                if spec is None:
                    raise RuntimeError(f"Unclassified Dagster HTTP route at runtime: {key!r}")
                return spec, child_scope.get("path_params", {})
        return None

    def _principal(self, scope: dict[str, Any]) -> Any:
        headers = {
            key.decode().lower(): value.decode()
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        client = scope.get("client")
        client_host = client[0] if isinstance(client, (tuple, list)) and client else None
        request = SimpleNamespace(
            headers=headers,
            scope=scope,
            path=scope.get("path", "/"),
            client=SimpleNamespace(host=client_host),
        )
        return self.middleware._extract_principal(
            SimpleNamespace(context=SimpleNamespace(request=request))
        )

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if not requires_http_authorization():
            await self.app(scope, receive, send)
            return
        matched = self._match(scope)
        if matched is None:
            await self.app(scope, receive, send)
            return
        spec, path_params = matched
        if spec.public:
            await self.app(scope, receive, send)
            return

        principal = self._principal(scope)
        request_id = next(
            (
                value.decode()
                for key, value in scope.get("headers", [])
                if key.lower() == b"x-request-id"
            ),
            str(uuid4()),
        )
        if principal is None:
            response = JSONResponse(
                {"error": "unauthorized", "reason": "authentication_required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer", "X-Request-Id": request_id},
            )
            await response(scope, receive, send)
            return

        authenticated_scope = dict(scope)
        authenticated_scope["phlo_authenticated_principal"] = principal

        if not spec.delegated_to_graphql:
            from phlo.capabilities import DecisionContext, ResourceRef
            from phlo.security import enforce

            if spec.resource_keys == ("path",):
                run_id = extract_dagster_run_id_from_log_path(path_params.get("path"))
                if run_id is None:
                    response = JSONResponse(
                        {"error": "forbidden", "reason": "access_denied"},
                        status_code=403,
                        headers={"X-Request-Id": request_id},
                    )
                    await response(scope, receive, send)
                    return
                resource_id = f"runId={run_id}"
            else:
                resource_id = (
                    "|".join(
                        f"{key}={path_params[key]}"
                        for key in spec.resource_keys
                        if path_params.get(key)
                    )
                    or "dagster"
                )
            result = enforce(
                principal=principal,
                action=spec.action,
                resource=ResourceRef(resource_type=spec.resource_type, resource_id=resource_id),
                context=DecisionContext(request_id=request_id),
                request_id=request_id,
                surface="dagster-webserver",
                correlation_id=request_id,
            )
            if result.variant == "error":
                response = JSONResponse(
                    {"error": "service_unavailable", "reason": "authorization_unavailable"},
                    status_code=503,
                    headers={"X-Request-Id": request_id},
                )
                await response(scope, receive, send)
                return
            if not result.allowed:
                response = JSONResponse(
                    {"error": "forbidden", "reason": "access_denied"},
                    status_code=403,
                    headers={"X-Request-Id": request_id},
                )
                await response(scope, receive, send)
                return
        await self.app(authenticated_scope, receive, send)


class GraphQLWebSocketAuthenticationASGI:
    """Authenticate graphql-ws connection_init before handing off to Dagster."""

    def __init__(self, app: Callable[..., Awaitable[None]], middleware) -> None:  # noqa: ANN001
        self.app = app
        self.middleware = middleware
        raw_timeout = os.environ.get(
            GRAPHQL_WS_INIT_TIMEOUT_ENV, str(_DEFAULT_GRAPHQL_WS_INIT_TIMEOUT)
        )
        try:
            self.connection_init_timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(f"{GRAPHQL_WS_INIT_TIMEOUT_ENV} must be numeric") from exc
        if not 0 < self.connection_init_timeout <= _MAX_GRAPHQL_WS_INIT_TIMEOUT:
            raise ValueError(
                f"{GRAPHQL_WS_INIT_TIMEOUT_ENV} must be greater than 0 and at most "
                f"{_MAX_GRAPHQL_WS_INIT_TIMEOUT:g}"
            )

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "websocket" or scope.get("path") != "/graphql":
            await self.app(scope, receive, send)
            return
        if not requires_http_authorization():
            await self.app(scope, receive, send)
            return

        offered_protocols = set(scope.get("subprotocols") or ())
        if GraphQLWS.PROTOCOL.value not in offered_protocols:
            await send({"type": "websocket.close", "code": 4406})
            return

        headers = {
            key.decode().lower(): value.decode()
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        if self._principal(scope, headers) is not None:
            await self.app(scope, receive, send)
            return

        connect = await receive()
        if connect.get("type") != "websocket.connect":
            await send({"type": "websocket.close", "code": 4401})
            return

        # A WebSocket peer cannot send connection_init until the HTTP upgrade
        # has been accepted. Dagster's endpoint accepts after it receives the
        # replayed connect event, so accept here and suppress that duplicate
        # downstream accept while preserving the negotiated protocol.
        await send(
            {
                "type": "websocket.accept",
                "subprotocol": GraphQLWS.PROTOCOL.value,
            }
        )
        try:
            init = await asyncio.wait_for(receive(), timeout=self.connection_init_timeout)
        except asyncio.TimeoutError:
            await send({"type": "websocket.close", "code": 4408})
            return
        payload = self._connection_init_payload(init)
        if payload is None:
            await send({"type": "websocket.close", "code": 4401})
            return

        authenticated_scope = dict(scope)
        authenticated_scope["phlo_graphql_connection_init"] = payload
        if self._principal(authenticated_scope, headers) is None:
            await send({"type": "websocket.close", "code": 4401})
            return

        replay = iter((connect, init))

        async def receive_with_replay():  # noqa: ANN202
            """Replay captured connect and init messages before live receives."""
            try:
                return next(replay)
            except StopIteration:
                return await receive()

        async def send_without_duplicate_accept(message):  # noqa: ANN001, ANN202
            """Forward sends while suppressing the upstream websocket accept."""
            if message.get("type") == "websocket.accept":
                return
            await send(message)

        await self.app(authenticated_scope, receive_with_replay, send_without_duplicate_accept)

    @staticmethod
    def _connection_init_payload(message: dict[str, Any]) -> dict[str, str] | None:
        if message.get("type") != "websocket.receive":
            return None
        raw = message.get("text")
        if raw is None and isinstance(message.get("bytes"), bytes):
            raw = message["bytes"].decode("utf-8", errors="replace")
        try:
            envelope = json.loads(raw) if isinstance(raw, str) else None
        except json.JSONDecodeError:
            return None
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if (
            not isinstance(envelope, dict)
            or envelope.get("type") != "connection_init"
            or not isinstance(payload, dict)
            or set(payload) != {"access_token"}
            or not isinstance(token, str)
            or not token.strip()
        ):
            return None
        return {"access_token": token}

    def _principal(self, scope: dict[str, Any], headers: dict[str, str]):
        client = scope.get("client")
        client_host = client[0] if isinstance(client, (tuple, list)) and client else None
        request = SimpleNamespace(
            headers=headers,
            scope=scope,
            path=scope.get("path", "/graphql"),
            client=SimpleNamespace(host=client_host),
        )
        info = SimpleNamespace(context=SimpleNamespace(request=request))
        return self.middleware._extract_principal(info)


class PhloDagsterWebserver(DagsterWebserver):
    """Dagster webserver with Phlo's mandatory GraphQL boundary installed."""

    def build_graphql_middleware(self) -> list:
        """Build the mandatory GraphQL authorization middleware stack.

        Return an empty list when GraphQL schema regulation is disabled. Raise
        RuntimeError when OIDC identity is required but the validator is not ready.
        """
        validate_graphql_schema(self._graphene_schema.graphql_schema)
        if not requires_http_authorization():
            return []
        discover_capabilities()
        get_adapter().install(self)
        middleware = self._get_graphql_authorization_middleware()
        if self._oidc_required() and not middleware._oidc_validator.configured:
            raise RuntimeError("Dagster OIDC identity is required but not ready")
        return [middleware]

    @staticmethod
    def _oidc_required() -> bool:
        return os.environ.get(OIDC_REQUIRED_ENV, "").strip().lower() == "true"

    async def webserver_info_endpoint(self, _request):  # noqa: ANN001, ANN201
        """Report webserver health, including OIDC validator readiness."""
        middleware = self._get_graphql_authorization_middleware()
        if self._oidc_required() and not middleware._oidc_validator.readiness():
            return JSONResponse(
                {"status": "unhealthy", "reason": "oidc_unready"},
                status_code=503,
            )
        return JSONResponse({"status": "healthy"})

    def create_asgi_app(self, **kwargs: Any):  # noqa: ANN202
        """Wrap the Dagster ASGI app with Phlo HTTP and WebSocket auth boundaries."""
        app = super().create_asgi_app(**kwargs)
        middleware = self._get_graphql_authorization_middleware()
        graphql_app = GraphQLWebSocketAuthenticationASGI(
            app,
            middleware,
        )
        return DagsterHTTPAuthenticationASGI(
            graphql_app,
            middleware,
            app.routes,
        )

    def _get_graphql_authorization_middleware(self) -> DagsterGraphQLAuthorizationMiddleware:
        middleware = getattr(self, "_phlo_graphql_authorization_middleware", None)
        if middleware is None:
            middleware = DagsterGraphQLAuthorizationMiddleware(
                nonce_store=self._build_durable_nonce_store()
            )
            self._phlo_graphql_authorization_middleware = middleware
        return middleware

    def _build_durable_nonce_store(self) -> PostgresNonceStore | None:
        """Build the webserver-owned durable nonce store once per process.

        Receivers requiring HTTP authorization initialize the durable schema
        through this lifecycle hook so replay state survives restarts and is
        shared across replicas. Development needs no nonce store at all.
        """
        if not requires_http_authorization():
            return None
        store = getattr(self, "_phlo_durable_nonce_store", None)
        if store is None:
            store = build_durable_nonce_store()
            if store is not None:
                store.ensure_schema()
            self._phlo_durable_nonce_store = store
        return store

    async def execute_graphql_subscription(
        self,
        websocket,
        operation_id: str,
        query: str,
        variables: dict | None,
        operation_name: str | None,
    ):
        """Authorize subscription roots before Dagster starts the async task."""
        if not requires_http_authorization():
            return await super().execute_graphql_subscription(
                websocket,
                operation_id,
                query,
                variables,
                operation_name,
            )
        middleware = self._get_graphql_authorization_middleware()
        try:
            document = parse(query)
            operations = [
                definition
                for definition in document.definitions
                if getattr(getattr(definition, "operation", None), "value", None) == "subscription"
                and (
                    operation_name is None
                    or getattr(getattr(definition, "name", None), "value", None) == operation_name
                )
            ]
            if len(operations) != 1:
                raise GraphQLError("Unclassified GraphQL operation")
            operation = operations[0]
            selection_set = getattr(operation, "selection_set", None)
            if selection_set is None:
                raise GraphQLError("GraphQL operation has no selection set")
            for selection in selection_set.selections:
                field_name = selection.name.value
                kwargs = {}
                for argument in selection.arguments:
                    value = argument.value
                    if value.kind == "variable":
                        kwargs[argument.name.value] = (variables or {}).get(value.name.value)
                    elif hasattr(value, "value"):
                        kwargs[argument.name.value] = value.value
                info = SimpleNamespace(
                    context=SimpleNamespace(request=websocket),
                    operation=operation,
                    field_name=field_name,
                    parent_type=SimpleNamespace(name="Subscription"),
                    path=SimpleNamespace(prev=None),
                )
                if not middleware._is_known_field(info):
                    raise GraphQLError(
                        "Unclassified GraphQL operation",
                        extensions={"code": "UNCLASSIFIED_OPERATION"},
                    )
                result = middleware._authorize_field(info, kwargs)
                if not result.allowed:
                    if result.reason_code == "authentication_required":
                        raise GraphQLError(
                            "Authentication required",
                            extensions={"code": "UNAUTHENTICATED"},
                        )
                    raise GraphQLError("Forbidden", extensions={"code": "FORBIDDEN"})
        except GraphQLError as error:
            return None, error.formatted
        except Exception:
            return None, GraphQLError(
                "Authorization check failed",
                extensions={"code": "AUTHORIZATION_UNAVAILABLE"},
            ).formatted

        return await super().execute_graphql_subscription(
            websocket,
            operation_id,
            query,
            variables,
            operation_name,
        )


# Dagster's CLI factory resolves this class from dagster_webserver.app, so the
# normal CLI options and workspace lifecycle remain unchanged while the shipped
# entrypoint uses the secured subclass.
setattr(dagster_app, "DagsterWebserver", PhloDagsterWebserver)


if __name__ == "__main__":
    dagster_webserver()
