"""GraphQL authorization middleware for Dagster webserver.

This middleware intercepts GraphQL requests to the Dagster webserver and
enforces authorization decisions via the core EnforcementContext.

Middleware Integration:
    - Dagster uses a repository-based workspace loading system
    - Middleware is added to the GraphQL schema
    - The DagsterRegulatedSurfaceAdapter.install() method handles middleware wiring

Authorization Flow:
    1. Extract bearer token from Authorization header
    2. Create DecisionContext with request metadata
    3. Map GraphQL operation to canonical action and resource
    4. Call enforce() via EnforcementContext
    5. Return GraphQL error if denied, proceed if allowed

Principal Extraction:
    - Phlo HMAC service token in Authorization for service-to-service calls
    - RS256 OIDC access token in Authorization or oauth2-proxy forwarding headers
    - Unsigned identity headers are never trusted
    - If neither present, deny with auth required

Operation Mapping:
    - Uses the operation name from the GraphQL request
    - Extracts asset key / run ID from operation variables
    - Maps to canonical action (asset.execute, run.read, etc.)
"""

from __future__ import annotations

import os
import re
import json
from collections.abc import Mapping
from typing import Any

from dagster import __version__ as DAGSTER_VERSION
from graphql import GraphQLError

from phlo.capabilities import (
    AuthPrincipal,
    DecisionContext,
    ResourceRef,
)
from phlo.logging import get_logger
from phlo.security import is_regulated
from phlo.security.enforcement import enforce
from phlo.security.service_identity import (
    PHLO_CORRELATION_HEADER,
    validate_service_token,
)
from phlo_dagster.authorization import (
    extract_dagster_run_id_from_log_key,
    resolve_graphql_operation,
)
from phlo_dagster.oidc_identity import OIDCIdentityValidator

logger = get_logger(__name__)

AUTHORIZATION_HEADER_RE = re.compile(r"Bearer\s+(.+)", re.IGNORECASE)
DAGSTER_USER_HEADER = "X-Dagster-User"
DAGSTER_API_TOKEN_HEADER = "X-Dagster-Api-Token"
DAGSTER_ALLOWED_SERVICES_ENV = "PHLO_DAGSTER_ALLOWED_SERVICE_IDS"


class DagsterGraphQLAuthorizationMiddleware:
    """GraphQL middleware that enforces authorization on Dagster requests.

    This middleware integrates with Dagster's GraphQL execution pipeline
    to apply authorization decisions before operations execute.

    Example:
        Adding middleware to Dagster workspace::

            from phlo_dagster.authorization_middleware import (
                DagsterGraphQLAuthorizationMiddleware,
            )

            middleware = DagsterGraphQLAuthorizationMiddleware()
    """

    def __init__(
        self,
        surface_name: str = "dagster-webserver",
        strict_mode: bool = True,
    ) -> None:
        """Set the surface name; strict mode is mandatory, so passing False raises."""
        if not strict_mode:
            raise ValueError("Dagster GraphQL authorization is mandatory and cannot fail open")
        self.surface_name = surface_name
        self.strict_mode = True
        self._oidc_validator = OIDCIdentityValidator()

    def resolve(
        self,
        next_fn: Any,
        root: Any,
        info: Any,
        **kwargs: Any,
    ) -> Any:
        """Resolve a GraphQL field after enforcing authorization on root operations.

        Non-root fields pass through untouched; a denied root operation
        raises GraphQLError before reaching the resolver.
        """
        regulated = is_regulated()
        try:
            if not regulated:
                logger.debug("dagster_graphql_mandatory_enforcement_non_regulated")
            if not self._is_root_operation_field(info):
                return next_fn(root, info, **kwargs)

            if not self._is_known_field(info):
                raise GraphQLError(
                    "Unclassified GraphQL operation",
                    extensions={"code": "UNCLASSIFIED_OPERATION"},
                )

            auth_result = self._authorize_field(info, kwargs)
            if not auth_result.allowed:
                logger.warning(
                    "dagster_authorization_denied",
                    operation=self._get_operation_name(info),
                    reason_code=auth_result.reason_code,
                    surface=self.surface_name,
                )
                if auth_result.reason_code == "authentication_required":
                    raise GraphQLError(
                        "Authentication required",
                        extensions={"code": "UNAUTHENTICATED"},
                    )
                raise GraphQLError("Forbidden", extensions={"code": "FORBIDDEN"})

            return next_fn(root, info, **kwargs)

        except GraphQLError:
            raise
        except Exception:
            # Fail closed: a middleware crash must never let the resolver run
            # unenforced, so convert any unexpected error into an auth failure.
            logger.exception("dagster_authorization_middleware_error")
            raise GraphQLError(
                "Authorization check failed",
                extensions={"code": "AUTHORIZATION_UNAVAILABLE"},
            )

    def _is_mutation(self, info: Any) -> bool:
        """Determine if the operation is a mutation."""
        operation = getattr(info, "operation", None)
        if operation and hasattr(operation, "operation"):
            return str(operation.operation.value) == "mutation"
        return False

    def _get_operation_name(self, info: Any) -> str | None:
        """Extract the operation name from the GraphQL info."""
        operation = getattr(info, "operation", None)
        if operation and hasattr(operation, "name") and operation.name:
            return operation.name.value
        return None

    def _get_mutation_field_name(self, info: Any) -> str | None:
        """Return the current GraphQL field name when available."""
        field_name = getattr(info, "field_name", None)
        if isinstance(field_name, str) and field_name:
            return field_name
        return None

    def _is_root_mutation_field(self, info: Any) -> bool:
        """Return True for top-level mutation fields only."""
        parent_type = getattr(info, "parent_type", None)
        if getattr(parent_type, "name", None) == "Mutation":
            return True
        path = getattr(info, "path", None)
        return getattr(path, "prev", None) is None

    def _is_root_query_field(self, info: Any) -> bool:
        """Return True for top-level query fields only."""
        parent_type = getattr(info, "parent_type", None)
        return getattr(parent_type, "name", None) == "Query"

    def _is_root_subscription_field(self, info: Any) -> bool:
        """Return True for top-level subscription fields."""
        parent_type = getattr(info, "parent_type", None)
        return getattr(parent_type, "name", None) == "Subscription"

    def _operation_kind(self, info: Any) -> str:
        operation = getattr(getattr(info, "operation", None), "operation", None)
        value = getattr(operation, "value", operation)
        if value in {"query", "mutation", "subscription"}:
            return value
        raise GraphQLError("Unclassified GraphQL operation")

    def _is_root_operation_field(self, info: Any) -> bool:
        return (
            self._is_root_query_field(info)
            or self._is_root_mutation_field(info)
            or self._is_root_subscription_field(info)
        )

    def _is_known_field(self, info: Any) -> bool:
        """Reject schema fields that are absent from the security registry."""
        field_name = self._get_mutation_field_name(info)
        if field_name in {"__schema", "__type"}:
            return True
        try:
            resolve_graphql_operation(self._operation_kind(info), field_name or "")
        except RuntimeError:
            return False
        return True

    def _audit_read_operation(self, info: Any) -> None:
        """Emit a lightweight audit event for read operations.

        Reads are always allowed but logged for regulated audit trails.
        """
        try:
            principal = self._extract_principal(info)
            field_name = self._get_mutation_field_name(info) or "unknown"
            action = self._map_query_to_action(field_name)
            decision_context = self._create_decision_context(info)

            from phlo.security.enforcement import EnforcementContext

            ctx = EnforcementContext.get_instance()
            ctx.audit_emitter.emit_authorization(
                surface=self.surface_name,
                action=action,
                resource_type=self._get_query_resource_type(field_name),
                resource_id=f"dagster:{field_name}",
                actor_subject=principal.subject if principal else "anonymous",
                actor_type=principal.principal_type if principal else "unknown",
                actor_roles=principal.groups if principal else (),
                authentication_source=(
                    principal.attributes.get("authentication_source", "unknown")
                    if principal
                    else "none"
                ),
                decision="allow",
                reason_code="read_access",
                policy_id=None,
                request_id=decision_context.request_id,
                correlation_id=decision_context.request_id,
            )
        except Exception:
            logger.debug("dagster_read_audit_skipped", exc_info=True)

    def _map_query_to_action(self, field_name: str) -> str:
        """Map a GraphQL query field name to a canonical read action."""
        return resolve_graphql_operation("query", field_name).action

    def _get_query_resource_type(self, field_name: str) -> str:
        """Map a GraphQL query field name to a resource type."""
        return resolve_graphql_operation("query", field_name).resource_type

    def _get_selection_resource(
        self,
        mutation_field_name: str | None,
    ) -> tuple[str, str | None]:
        """Return the mutation's classified resource type with a null resource id."""
        if not mutation_field_name:
            raise GraphQLError("Unclassified GraphQL operation")
        spec = resolve_graphql_operation("mutation", mutation_field_name)
        return spec.resource_type, None

    def _extract_principal(self, info: Any) -> AuthPrincipal | None:
        """Extract the authenticated principal from request headers or websocket auth.

        Prefers the middleware-authenticated ASGI scope principal, then the
        graphql-ws connection_init token, then bearer/service tokens and
        forwarded access-token headers.
        """
        context = getattr(info, "context", None)
        if context is None:
            return None

        request = getattr(context, "request", None)
        if request is None:
            request = getattr(context, "wsgi_request", None)
        if request is None:
            # Dagster stores the Starlette request as the private request-context
            # source rather than exposing it as ``context.request``.
            request = getattr(context, "_source", None)
        if request is None:
            return None

        scope = getattr(request, "scope", None)
        if isinstance(scope, Mapping):
            principal = scope.get("phlo_authenticated_principal")
            if isinstance(principal, AuthPrincipal):
                return principal

        connection_init = self._graphql_connection_init(request)
        if connection_init is not None:
            # Browser graphql-ws clients authenticate in connection_init. Do
            # not fall back to unsigned connection metadata or user headers.
            token = connection_init.get("access_token")
            if not isinstance(token, str) or not token.strip():
                return None
            return self._oidc_validator.validate(token)

        headers: dict[str, str] = {}
        if hasattr(request, "headers"):
            headers = {str(k).lower(): str(v) for k, v in request.headers.items()}
        elif hasattr(request, "META"):
            headers = {
                k.lower().replace("http_", "").replace("_", "-"): v
                for k, v in request.META.items()
                if k.startswith("HTTP_")
            }

        auth_header = headers.get("authorization", "")
        match = AUTHORIZATION_HEADER_RE.match(auth_header)
        if match:
            token = match.group(1)
            service_id = validate_service_token(token)
            allowed_services = {
                value.strip()
                for value in os.environ.get(DAGSTER_ALLOWED_SERVICES_ENV, "phlo-api").split(",")
                if value.strip()
            }
            if service_id and service_id in allowed_services:
                return AuthPrincipal(
                    subject=f"service:{service_id}",
                    principal_type="service",
                    groups=(),
                    attributes={"authentication_source": "service_token"},
                )
            oidc_principal = self._oidc_validator.validate(token)
            if oidc_principal is not None:
                return oidc_principal
            return None

        access_token = headers.get("x-auth-request-access-token") or headers.get(
            "x-forwarded-access-token"
        )
        if access_token:
            return self._oidc_validator.validate(access_token)

        return None

    @staticmethod
    def _graphql_connection_init(request: Any) -> dict[str, Any] | None:
        """Read the strict graphql-ws connection_init token envelope."""
        payload = getattr(request, "graphql_connection_init", None)
        scope = getattr(request, "scope", None)
        if payload is None and isinstance(scope, dict):
            payload = scope.get("phlo_graphql_connection_init")
        if payload is None:
            return None
        return payload if isinstance(payload, dict) else {}

    def _create_decision_context(self, info: Any) -> DecisionContext:
        """Build a DecisionContext with request id, client IP, and operation metadata."""
        context = getattr(info, "context", None)

        request_id: str | None = None
        ip_address: str | None = None

        if context is not None:
            request = getattr(context, "request", None) or getattr(context, "wsgi_request", None)
            if request is not None:
                request_headers = {
                    str(k).lower(): str(v) for k, v in getattr(request, "headers", {}).items()
                }
                request_id = (
                    request_headers.get(PHLO_CORRELATION_HEADER.lower())
                    or getattr(request, "id", None)
                    or getattr(request, "request_id", None)
                )
                ip_address = getattr(request, "remote_addr", None) or getattr(
                    request, "client_ip", None
                )

        operation_name = self._get_operation_name(info)
        return DecisionContext(
            request_id=request_id,
            ip_address=ip_address,
            attributes={
                "graphql_operation": operation_name or "unknown",
                "dagster_version": DAGSTER_VERSION,
            },
        )

    def _authorize_mutation(
        self,
        info: Any,
        kwargs: dict[str, Any],
    ):
        """Authorize a GraphQL mutation through core enforcement."""
        return self._authorize_field(info, kwargs)

    def _authorize_field(self, info: Any, kwargs: dict[str, Any]):
        """Authorize every root GraphQL query and mutation before resolution."""
        from phlo.security.adapters import EnforcementResult

        principal = self._extract_principal(info)
        if principal is None:
            return EnforcementResult.deny(
                reason_code="authentication_required",
                explanation="No valid authentication credentials provided",
            )

        field_name = self._get_mutation_field_name(info)
        operation_kind = self._operation_kind(info)
        spec = resolve_graphql_operation(operation_kind, field_name or "")
        context = self._create_decision_context(info)
        resource_ids = self._graphql_resource_ids(spec, field_name, kwargs)
        if not resource_ids:
            raise GraphQLError(
                "GraphQL operation is missing its authoritative resource identity",
                extensions={"code": "AUTHORIZATION_UNAVAILABLE"},
            )
        results = [
            enforce(
                principal=principal,
                action=spec.action,
                resource=ResourceRef(
                    resource_type=spec.resource_type,
                    resource_id=resource_id,
                ),
                context=context,
                request_id=context.request_id,
                surface=self.surface_name,
                correlation_id=context.request_id,
            )
            for resource_id in resource_ids
        ]
        for result in results:
            if not result.allowed:
                return result
        return results[-1]

    def _graphql_resource_ids(
        self,
        spec: Any,
        field_name: str | None,
        kwargs: dict[str, Any],
    ) -> list[str]:
        """Build an identity from allow-listed leaf values in typed arguments.

        Dagster puts launch, reexecution, and selector values inside input
        objects, so checking only the top-level resolver kwargs would authorize
        every operation against the field name instead of its target.
        """
        if field_name in {
            "assetNodeAdditionalRequiredKeys",
            "assetNodeDefinitionCollisions",
            "assetNodes",
            "assetsLatestInfo",
            "assetsOrError",
            "terminateRuns",
            "wipeAssets",
        }:
            key = "runIds" if field_name == "terminateRuns" else "assetKey"
            if field_name not in {"terminateRuns", "wipeAssets"}:
                key = "assetKeys"
            values = self._find_graphql_values(kwargs, key)
            flattened = [item for value in values for item in self._flatten_bulk_value(value)]
            return [
                f"{('runId' if key == 'runIds' else 'assetKey')}="
                f"{self._serialize_resource_value(value)}"
                for value in flattened
            ]
        if field_name == "launchMultipleRuns":
            targets = self._find_graphql_values(kwargs, "executionParamsList")
            flattened_targets = [
                item for value in targets for item in self._flatten_bulk_value(value)
            ]
            return [
                self._graphql_resource_id_for_values(spec, field_name, target)
                for target in flattened_targets
            ]
        return [self._graphql_resource_id_for_values(spec, field_name, kwargs)]

    def _graphql_resource_id_for_values(
        self,
        spec: Any,
        field_name: str | None,
        kwargs: dict[str, Any],
    ) -> str:
        """Build one composite identity from one typed GraphQL target."""
        values = []
        for key in spec.keys_for_field(field_name or ""):
            found_values = self._find_graphql_values(kwargs, key)
            if key == "logKey":
                run_ids = [
                    run_id
                    for value in found_values
                    if (run_id := extract_dagster_run_id_from_log_key(value)) is not None
                ]
                distinct_run_ids = list(dict.fromkeys(run_ids))
                if len(distinct_run_ids) != len(found_values) or len(distinct_run_ids) != 1:
                    raise GraphQLError(
                        "Malformed or ambiguous Dagster log key",
                        extensions={"code": "AUTHORIZATION_UNAVAILABLE"},
                    )
                values.append(f"runId={distinct_run_ids[0]}")
                continue
            serialized_values = [self._serialize_resource_value(value) for value in found_values]
            distinct_values = list(dict.fromkeys(serialized_values))
            if len(distinct_values) > 1:
                raise GraphQLError(
                    "Ambiguous GraphQL resource identity",
                    extensions={"code": "AUTHORIZATION_UNAVAILABLE"},
                )
            if distinct_values:
                values.append(f"{key}={distinct_values[0]}")
        if not values and getattr(spec, "require_resource", False):
            raise GraphQLError(
                "GraphQL operation is missing its authoritative resource identity",
                extensions={"code": "AUTHORIZATION_UNAVAILABLE"},
            )
        return "|".join(values) if values else f"dagster:{field_name or 'operation'}"

    def _find_graphql_values(self, value: Any, key: str) -> list[Any]:
        """Collect all allow-listed values so conflicting IDs fail closed."""
        if isinstance(value, Mapping):
            found: list[Any] = []
            if key in value and value[key] is not None:
                found.append(value[key])
            for nested in value.values():
                found.extend(self._find_graphql_values(nested, key))
            return found
        if isinstance(value, (list, tuple)):
            found = []
            for nested in value:
                found.extend(self._find_graphql_values(nested, key))
            return found
        return []

    @staticmethod
    def _flatten_bulk_value(value: Any) -> list[Any]:
        """Expand typed lists and Dagster's JSON-encoded list scalar."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return [value]
            if isinstance(decoded, list):
                return decoded
        return [value]

    @staticmethod
    def _serialize_resource_value(value: Any) -> str:
        """Serialize resource values without relying on dict repr ordering."""
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        if isinstance(value, (list, tuple, dict)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return str(value)

    def _map_operation_to_action(self, mutation_field_name: str | None) -> str:
        """Return the canonical action for a classified mutation field name."""
        if not mutation_field_name:
            raise GraphQLError("Unclassified GraphQL operation")
        return resolve_graphql_operation("mutation", mutation_field_name).action
