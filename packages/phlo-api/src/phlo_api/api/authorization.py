"""Authorization helpers for phlo-api.

This module provides authorization capability integration for FastAPI routes.
It implements role-based access control (RBAC) with canonical role mapping
from authentication groups.

In regulated mode, authorization is delegated to the core EnforcementContext
singleton which owns canonicalization, PDP decisions, and audit emission.
In non-regulated mode, the local authorization backend is used.

Key Functions:
    get_authorization_backend: Resolve the configured authorization backend.
    check_dataset_read: Verify read permission on a dataset.
    check_dataset_query: Verify query permission on a dataset.
    check_asset_read: Verify read permission on an asset.
    filter_datasets: Filter datasets by access permission.

Environment Variables:
    PHLO_AUTHORIZATION_BACKEND: Name of the authorization backend to use.
        Required when multiple backends are installed.
    PHLO_REGULATED_MODE: Enable regulated mode (enables core enforcement).

Example:
    Enforcing authorization in a FastAPI route:

    .. code-block:: python

        from fastapi import Request
        from phlo_api.api.authorization import check_dataset_read

        @app.get("/datasets/{dataset_id}")
        async def get_dataset(dataset_id: str, request: Request):
            check_dataset_read(request, dataset_id)
            return {"dataset": dataset_id}

"""

from __future__ import annotations

import os
from uuid import uuid4
from typing import Any, Callable, TypeVar

from fastapi import HTTPException, Request

from phlo.capabilities import (
    AuthPrincipal,
    AuthorizationDecision,
    AuthorizationPolicyBackend,
    DecisionContext,
    Principal,
    ResourceRef,
    list_capabilities,
    resolve_capability,
)
from phlo.logging import get_logger
from phlo.security import enforce, is_regulated
from phlo.infrastructure.config import (
    get_api_authorization_config,
    get_configured_authorization_backend_name,
)
from phlo.security.service_identity import build_service_headers

from phlo_api.api.authentication import get_request_principal

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_ACTION_DATASET_READ = "dataset.read"
_ACTION_DATASET_QUERY = "dataset.query"
_ACTION_ASSET_READ = "asset.read"
_ACTION_ASSET_EXECUTE = "asset.execute"
_ACTION_SERVICE_READ = "service.read"
_ACTION_SERVICE_MANAGE = "service.manage"
_ACTION_ADMIN_READ = "admin.read"
_ACTION_ADMIN_MANAGE = "admin.manage"
_AUTHORIZATION_BACKEND_ENV = "PHLO_AUTHORIZATION_BACKEND"
_AUTHORIZATION_MODE_ENV = "PHLO_AUTHORIZATION_MODE"
_AUTHORIZATION_MODE_OPTIONAL = "optional"
_AUTHORIZATION_MODE_REQUIRED = "required"


def _configured_authorization_backend_name() -> str | None:
    """Resolve the same backend name used by validation and core enforcement."""
    try:
        return get_configured_authorization_backend_name()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def get_authorization_backend() -> AuthorizationPolicyBackend | None:
    """Resolve the authorization policy backend capability.

    Returns None if no backend is configured. Raises RuntimeError when the
    configured backend is not registered or multiple backends are installed
    without an explicit selection.
    """
    backend_name = _configured_authorization_backend_name()
    result = resolve_capability("authorization_policy_backend", backend_name)
    if backend_name and result is None:
        raise RuntimeError(
            f"Authorization backend {backend_name!r} is not registered. "
            f"Set {_AUTHORIZATION_BACKEND_ENV} to a valid backend name."
        )

    if result is None:
        available_backends = list_capabilities("authorization_policy_backend")
        if not available_backends:
            logger.debug("no_authorization_backend_configured")
            return None
        if backend_name is None and len(available_backends) > 1:
            raise RuntimeError(
                "Multiple authorization backends are registered. "
                f"Set {_AUTHORIZATION_BACKEND_ENV} to one of: {', '.join(sorted(available_backends))}."
            )
        logger.debug("no_authorization_backend_configured")
        return None
    return result.provider


def require_authorization_backend() -> AuthorizationPolicyBackend:
    """Resolve the authorization policy backend or raise if not available."""
    backend = get_authorization_backend()
    if backend is None:
        raise RuntimeError("Authorization backend not configured")
    return backend


def get_authorization_mode() -> str:
    """Return how route guards behave when no authorization backend exists."""
    configured_mode = os.environ.get(_AUTHORIZATION_MODE_ENV)
    if configured_mode is not None:
        configured_mode = configured_mode.strip() or None
    if configured_mode is None:
        config = get_api_authorization_config()
        configured_mode = (
            config.mode
            if config is not None and config.mode is not None
            else _AUTHORIZATION_MODE_OPTIONAL
        )

    mode = configured_mode.strip().lower()
    if mode not in {_AUTHORIZATION_MODE_OPTIONAL, _AUTHORIZATION_MODE_REQUIRED}:
        raise RuntimeError(
            f"Invalid {_AUTHORIZATION_MODE_ENV} value {mode!r}. "
            f"Expected {_AUTHORIZATION_MODE_OPTIONAL!r} or {_AUTHORIZATION_MODE_REQUIRED!r}."
        )
    return mode


def _get_backend_for_route_guard() -> AuthorizationPolicyBackend | None:
    """Return the backend used by route guards or raise in strict mode."""
    backend = get_authorization_backend()
    if backend is not None:
        return backend
    if get_authorization_mode() == _AUTHORIZATION_MODE_OPTIONAL:
        return None

    logger.warning(
        "authorization_backend_required_but_not_configured",
        mode=_AUTHORIZATION_MODE_REQUIRED,
    )
    raise HTTPException(
        status_code=503,
        detail={
            "error": "service_unavailable",
            "reason": "authorization_backend_not_configured",
        },
    )


def create_decision_context(
    request: Request,
    environment: str | None = None,
) -> DecisionContext:
    """Create a DecisionContext populated from request metadata."""
    return DecisionContext(
        environment=environment,
        request_id=get_request_correlation_id(request),
        ip_address=request.client.host if request.client else None,
        attributes={
            "method": request.method,
            "path": request.url.path,
        },
    )


def get_request_correlation_id(request: Request) -> str:
    """Return the request correlation ID, generating one when missing."""
    state = getattr(request, "state", None)
    request_id = getattr(state, "request_id", None) if state is not None else None
    if not request_id:
        request_id = request.headers.get("x-request-id") or str(uuid4())
        if state is not None:
            setattr(state, "request_id", request_id)
    return request_id


def build_downstream_service_headers(request: Request, service_id: str) -> dict[str, str]:
    """Build authenticated headers for service-to-service requests from phlo-api."""
    correlation_id = get_request_correlation_id(request)
    initiator = None
    auth_principal = get_request_principal(request)
    if auth_principal is not None:
        initiator = auth_principal.subject
    return build_service_headers(
        service_id=service_id,
        initiator=initiator,
        correlation_id=correlation_id,
    )


def resolve_request_principal(request: Request, require_auth: bool = False) -> Principal | None:
    """Resolve the Principal from the request via the authentication provider.

    Falls back to an anonymous principal unless require_auth is set. In
    regulated mode canonicalization happens inside the enforcement call,
    not here.
    """
    auth_principal = get_request_principal(request)
    if auth_principal is None:
        if require_auth:
            return None
        return Principal(
            subject="anonymous",
            principal_type="user",
            roles=(),
        )

    from phlo.identity.bridge import canonicalize_principal

    return canonicalize_principal(auth_principal, regulated=False)


def _enforce_or_raise(
    request: Request,
    action: str,
    resource: ResourceRef,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Enforce authorization via core EnforcementContext or raise HTTPException."""
    auth_principal = get_request_principal(request)
    if auth_principal is None:
        if require_auth:
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthorized", "reason": "authentication_required"},
            )
        principal = AuthPrincipal(
            subject="anonymous",
            principal_type="user",
            groups=(),
        )
    else:
        principal = auth_principal

    context = create_decision_context(request, environment)
    correlation_id = get_request_correlation_id(request)
    result = enforce(
        principal=principal,
        action=action,
        resource=resource,
        context=context,
        request_id=correlation_id,
        surface="phlo-api",
        correlation_id=correlation_id,
    )

    if result.variant == "error":
        logger.error(
            "authorization_backend_error",
            principal=principal.subject,
            action=action,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            reason_code=result.reason_code,
        )
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "reason": result.reason_code or "unknown"},
        )

    if not result.allowed:
        logger.warning(
            "authorization_denied",
            principal=principal.subject,
            action=action,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            reason_code=result.reason_code,
        )
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": result.reason_code or "explicit_deny"},
        )


def check_dataset_read(
    request: Request,
    dataset_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can read the dataset."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_DATASET_READ,
            ResourceRef(resource_type="dataset", resource_id=dataset_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="dataset",
        resource_id=dataset_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_DATASET_READ, resource, context):
        decision = backend.explain_decision(principal, _ACTION_DATASET_READ, resource, context)
        _log_deny(principal, _ACTION_DATASET_READ, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_dataset_query(
    request: Request,
    dataset_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can query the dataset."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_DATASET_QUERY,
            ResourceRef(resource_type="dataset", resource_id=dataset_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="dataset",
        resource_id=dataset_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_DATASET_QUERY, resource, context):
        decision = backend.explain_decision(principal, _ACTION_DATASET_QUERY, resource, context)
        _log_deny(principal, _ACTION_DATASET_QUERY, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_asset_read(
    request: Request,
    asset_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can read the asset."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_ASSET_READ,
            ResourceRef(resource_type="asset", resource_id=asset_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="asset",
        resource_id=asset_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_ASSET_READ, resource, context):
        decision = backend.explain_decision(principal, _ACTION_ASSET_READ, resource, context)
        _log_deny(principal, _ACTION_ASSET_READ, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_asset_execute(
    request: Request,
    asset_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can execute the asset."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_ASSET_EXECUTE,
            ResourceRef(resource_type="asset", resource_id=asset_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="asset",
        resource_id=asset_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_ASSET_EXECUTE, resource, context):
        decision = backend.explain_decision(principal, _ACTION_ASSET_EXECUTE, resource, context)
        _log_deny(principal, _ACTION_ASSET_EXECUTE, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_service_read(
    request: Request,
    service_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can read the service."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_SERVICE_READ,
            ResourceRef(resource_type="service", resource_id=service_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="service",
        resource_id=service_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_SERVICE_READ, resource, context):
        decision = backend.explain_decision(principal, _ACTION_SERVICE_READ, resource, context)
        _log_deny(principal, _ACTION_SERVICE_READ, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_service_manage(
    request: Request,
    service_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can manage the service."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_SERVICE_MANAGE,
            ResourceRef(resource_type="service", resource_id=service_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="service",
        resource_id=service_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_SERVICE_MANAGE, resource, context):
        decision = backend.explain_decision(principal, _ACTION_SERVICE_MANAGE, resource, context)
        _log_deny(principal, _ACTION_SERVICE_MANAGE, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_admin_read(
    request: Request,
    admin_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can read admin resources."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_ADMIN_READ,
            ResourceRef(resource_type="admin", resource_id=admin_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="admin",
        resource_id=admin_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_ADMIN_READ, resource, context):
        decision = backend.explain_decision(principal, _ACTION_ADMIN_READ, resource, context)
        _log_deny(principal, _ACTION_ADMIN_READ, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def check_admin_manage(
    request: Request,
    admin_id: str,
    environment: str | None = None,
    require_auth: bool = True,
) -> None:
    """Check if the request can manage admin resources."""
    if is_regulated():
        _enforce_or_raise(
            request,
            _ACTION_ADMIN_MANAGE,
            ResourceRef(resource_type="admin", resource_id=admin_id),
            environment,
            require_auth=require_auth,
        )
        return

    backend = _get_backend_for_route_guard()
    if backend is None:
        return

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "reason": "authentication_required"},
        )
    resource = ResourceRef(
        resource_type="admin",
        resource_id=admin_id,
    )
    context = create_decision_context(request, environment)

    if not backend.is_allowed(principal, _ACTION_ADMIN_MANAGE, resource, context):
        decision = backend.explain_decision(principal, _ACTION_ADMIN_MANAGE, resource, context)
        _log_deny(principal, _ACTION_ADMIN_MANAGE, resource, decision)
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "reason": decision.reason_code},
        )


def filter_datasets(
    request: Request,
    dataset_ids: list[str],
    action: str = _ACTION_DATASET_READ,
    environment: str | None = None,
    require_auth: bool = True,
) -> list[str]:
    """Filter a list of dataset IDs to only those the principal can access."""
    if is_regulated():
        auth_principal = get_request_principal(request)
        if auth_principal is None:
            return []

        context = create_decision_context(request, environment)
        allowed = []
        for d_id in dataset_ids:
            resource = ResourceRef(resource_type="dataset", resource_id=d_id)
            correlation_id = get_request_correlation_id(request)
            result = enforce(
                principal=auth_principal,
                action=action,
                resource=resource,
                context=context,
                request_id=correlation_id,
                surface="phlo-api",
                correlation_id=correlation_id,
            )
            if result.allowed:
                allowed.append(d_id)
        return allowed

    backend = _get_backend_for_route_guard()
    if backend is None:
        return dataset_ids

    principal = resolve_request_principal(request, require_auth=require_auth)
    if principal is None:
        return []
    resources = [ResourceRef(resource_type="dataset", resource_id=d_id) for d_id in dataset_ids]
    context = create_decision_context(request, environment)

    allowed_resources = backend.filter_resources(principal, resources, action, context)
    return [r.resource_id for r in allowed_resources]


def _log_deny(
    principal: Principal,
    action: str,
    resource: ResourceRef,
    decision: AuthorizationDecision,
) -> None:
    """Log authorization denial for auditing."""
    logger.warning(
        "authorization_denied",
        principal=principal.subject,
        principal_type=principal.principal_type,
        roles=list(principal.roles),
        action=action,
        resource_type=resource.resource_type,
        resource_id=resource.resource_id,
        reason_code=decision.reason_code,
        policy_id=decision.policy_id,
    )
