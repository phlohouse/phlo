"""Environment-scoped read-only v1 identity, service and event resources."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from time import monotonic
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from phlo.capabilities import AuthPrincipal, Principal, ResourceRef
from phlo.config.env import project_env_value
from phlo.plugins.discovery import ServiceDiscovery
from phlo.security import enforce, is_regulated
from phlo.security.enforcement import EnforcementContext
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.authorization import (
    create_decision_context,
    get_authorization_backend,
    resolve_request_principal,
)
from phlo_api.errors import (
    BadGatewayError,
    BackendUnavailableError,
    BadInputError,
    UnprocessableInputError,
    error_envelope,
)
from phlo_api.observatory_api.dagster import graphql_request, resolve_dagster_url
from phlo_api.observatory_api.http_client import backend_client
from phlo_api.v1_contract import (
    Environment,
    EnvironmentTarget,
    EnvironmentsResponse,
    MeResponse,
    ReadPermission,
    RunStatusEvent,
    ServiceSnapshot,
    ServicesResponse,
)

router = APIRouter(tags=["v1"])

_LOCATION_QUERY = """query Locations {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection { nodes { location { name } } }
  }
}"""
_RUN_QUERY = """query RecentRuns {
  runsOrError(limit: 100) {
    __typename
    ... on Runs {
      results { runId status repositoryOrigin { repositoryLocationName } }
    }
  }
}"""
_RUN_STATUSES = frozenset(
    {
        "NOT_STARTED",
        "MANAGED",
        "QUEUED",
        "STARTING",
        "STARTED",
        "SUCCESS",
        "FAILURE",
        "CANCELING",
        "CANCELED",
    }
)


def _targets() -> dict[str, EnvironmentTarget]:
    """Read the operator-owned allowlist, refusing partial or overlapping mappings."""
    try:
        raw = json.loads(os.environ["PHLO_V1_ENVIRONMENTS"])
        if not isinstance(raw, dict) or set(raw) != {"prod", "staging"}:
            raise ValueError("both environments must be configured")
        targets = {name: EnvironmentTarget.model_validate(value) for name, value in raw.items()}
        if (
            len({target.dagster_location for target in targets.values()}) != 2
            or len({target.nessie_ref for target in targets.values()}) != 2
        ):
            raise ValueError("environment targets must be distinct")
        if any(
            not re.fullmatch(r"[A-Za-z0-9_.-]+", target.nessie_ref) for target in targets.values()
        ):
            raise ValueError("invalid Nessie reference")
        return targets
    except (KeyError, ValueError, TypeError, ValidationError) as exc:
        raise BackendUnavailableError("Environment mapping is unavailable.") from exc


def _target(
    request: Request, env: Environment, *, allowed_query: frozenset[str] = frozenset({"env"})
) -> EnvironmentTarget:
    if not set(request.query_params) <= allowed_query:
        raise BadInputError("Only the env selector is accepted.")
    if request.query_params.getlist("env") != [env]:
        raise UnprocessableInputError("Provide exactly one environment selector.")
    return _targets()[env]


def _source_data(payload: Any, field: str, typename: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("errors"):
        raise BadGatewayError("Dagster returned an invalid response.")
    data = payload.get("data")
    value = data.get(field) if isinstance(data, dict) else None
    if not isinstance(value, dict) or value.get("__typename") != typename:
        raise BadGatewayError("Dagster returned an invalid response.")
    return value


async def _locations() -> set[str]:
    try:
        payload = await graphql_request(resolve_dagster_url(), _LOCATION_QUERY)
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        raise BackendUnavailableError("Dagster is unavailable.") from exc
    value = _source_data(payload, "repositoriesOrError", "RepositoryConnection")
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or any(
        not isinstance(node, dict)
        or not isinstance(node.get("location"), dict)
        or not isinstance(node["location"].get("name"), str)
        for node in nodes
    ):
        raise BadGatewayError("Dagster returned an invalid location list.")
    return {node["location"]["name"] for node in nodes}


async def _runs(location: str) -> dict[str, str]:
    try:
        payload = await graphql_request(resolve_dagster_url(), _RUN_QUERY)
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        raise BackendUnavailableError("Dagster is unavailable.") from exc
    value = _source_data(payload, "runsOrError", "Runs")
    rows = value.get("results")
    if not isinstance(rows, list):
        raise BadGatewayError("Dagster returned an invalid run list.")
    runs: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("repositoryOrigin"), dict):
            raise BadGatewayError("Dagster run has no location identity.")
        origin = row["repositoryOrigin"].get("repositoryLocationName")
        if (
            not isinstance(origin, str)
            or not isinstance(row.get("runId"), str)
            or row.get("status") not in _RUN_STATUSES
        ):
            raise BadGatewayError("Dagster returned an invalid run.")
        if origin == location:
            runs[row["runId"]] = row["status"]
    return runs


async def _service_snapshots(target: EnvironmentTarget) -> list[ServiceSnapshot]:
    try:
        definitions = ServiceDiscovery().discover()
    except Exception as exc:
        raise BackendUnavailableError("Service discovery is unavailable.") from exc
    if not definitions:
        raise BackendUnavailableError("Service discovery is unavailable.")

    # Definitions are identities, not health evidence. Only the two scoped
    # sources below have an environment-specific probe in this phase.
    items = {
        name: ServiceSnapshot(
            id=name, status="unknown", observed_at=None, response_time_seconds=None
        )
        for name in definitions
    }
    started = monotonic()
    try:
        locations = await _locations()
        dagster = ServiceSnapshot(
            id="dagster",
            status="healthy" if target.dagster_location in locations else "unhealthy",
            observed_at=datetime.now(timezone.utc),
            response_time_seconds=monotonic() - started,
        )
    except (BackendUnavailableError, BadGatewayError):
        dagster = ServiceSnapshot(
            id="dagster", status="unavailable", observed_at=None, response_time_seconds=None
        )
    items["dagster"] = dagster

    url = project_env_value("NESSIE_URL")
    if not url:
        items["nessie"] = ServiceSnapshot(
            id="nessie", status="unavailable", observed_at=None, response_time_seconds=None
        )
    else:
        base = url.rstrip("/")
        started = monotonic()
        try:
            async with backend_client() as client:
                response = await client.get(f"{base}/trees/{target.nessie_ref}", timeout=5.0)
            if response.status_code == 404:
                status = "unhealthy"
            else:
                response.raise_for_status()
                payload = response.json()
                ref = payload.get("reference") if isinstance(payload, dict) else None
                status = (
                    "healthy"
                    if isinstance(ref, dict)
                    and ref.get("name") == target.nessie_ref
                    and ref.get("type") in {"BRANCH", "TAG"}
                    else "unavailable"
                )
            items["nessie"] = ServiceSnapshot(
                id="nessie",
                status=status,
                observed_at=datetime.now(timezone.utc) if status != "unavailable" else None,
                response_time_seconds=monotonic() - started if status != "unavailable" else None,
            )
        except (httpx.HTTPError, ValueError):
            items["nessie"] = ServiceSnapshot(
                id="nessie", status="unavailable", observed_at=None, response_time_seconds=None
            )
    if len(items) > 500:
        raise BackendUnavailableError("Service inventory exceeds the v1 page limit.")
    return [items[name] for name in sorted(items)]


def _canonical(request: Request, principal: AuthPrincipal) -> Principal:
    if is_regulated():
        try:
            return EnforcementContext.get_instance().canonicalize(principal)
        except Exception as exc:
            raise BackendUnavailableError("Identity authority is unavailable.") from exc
    canonical = resolve_request_principal(request, require_auth=True)
    assert canonical is not None  # Enforced by the v1 manifest boundary.
    return canonical


def _permissions(request: Request) -> dict[Environment, list[ReadPermission]]:
    principal = get_request_principal(request)
    assert principal is not None  # Enforced by the v1 manifest boundary.
    canonical = _canonical(request, principal)
    permissions: dict[Environment, list[ReadPermission]] = {}
    backend = None if is_regulated() else get_authorization_backend()
    environments: tuple[Environment, ...] = ("prod", "staging")
    actions: tuple[tuple[ReadPermission, str], ...] = (
        ("service.read", "service"),
        ("run.read", "run"),
    )
    for env in environments:
        permissions[env] = []
        for action, resource_type in actions:
            resource = ResourceRef(resource_type=resource_type, resource_id=f"env={env}")
            context = create_decision_context(request, env)
            if is_regulated():
                try:
                    decision = enforce(
                        principal=principal,
                        action=action,
                        resource=resource,
                        context=context,
                        request_id=request.state.request_id,
                        surface="phlo-api",
                        correlation_id=request.state.request_id,
                    )
                except Exception as exc:
                    raise BackendUnavailableError("Authorization is unavailable.") from exc
                if decision.variant == "error":
                    raise BackendUnavailableError("Authorization is unavailable.")
                allowed = decision.allowed
            else:
                if backend is None:
                    raise BackendUnavailableError("Authorization is unavailable.")
                try:
                    decision = backend.explain_decision(canonical, action, resource, context)
                except Exception as exc:
                    raise BackendUnavailableError("Authorization is unavailable.") from exc
                if decision.reason_code == "backend_unavailable":
                    raise BackendUnavailableError("Authorization is unavailable.")
                allowed = decision.allowed
            if allowed:
                permissions[env].append(action)
    return permissions


@router.get("/me", response_model=MeResponse)
def v1_me(request: Request) -> MeResponse:
    principal = get_request_principal(request)
    assert principal is not None
    canonical = _canonical(request, principal)
    return MeResponse.model_validate(
        {
            "subject": principal.subject,
            "principal_type": principal.principal_type,
            "email": principal.email,
            "roles": list(canonical.roles),
            "permissions": _permissions(request),
        }
    )


@router.get("/environments", response_model=EnvironmentsResponse)
async def v1_environments(request: Request) -> EnvironmentsResponse:
    targets = _targets()
    items = []
    permissions = _permissions(request)
    for env in ("prod", "staging"):
        if "service.read" not in permissions[env]:
            continue
        sources = {item.id: item.status for item in await _service_snapshots(targets[env])}
        items.append(
            {
                "env": env,
                "status": "available"
                if sources["dagster"] == sources["nessie"] == "healthy"
                else "unavailable",
            }
        )
    if not items:
        raise HTTPException(status_code=403, detail="Access denied.")
    return EnvironmentsResponse.model_validate({"items": items})


@router.get("/services", response_model=ServicesResponse)
async def v1_services(request: Request, env: Annotated[Environment, Query()]) -> ServicesResponse:
    return ServicesResponse(
        env=env, items=await _service_snapshots(_target(request, env)), next_cursor=None
    )


def _frame(kind: str, data: dict[str, Any], cursor: str | None = None) -> str:
    prefix = f"id: {cursor}\n" if cursor else ""
    return f"{prefix}event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def v1_events(request: Request, env: Annotated[Environment, Query()]) -> StreamingResponse:
    if request.headers.get("last-event-id"):
        raise BadInputError(
            "Event replay is not supported; resync from /services.", code="resync_required"
        )
    target = _target(request, env)
    # Preflight both sources before returning streaming headers.
    services = await _service_snapshots(target)
    if any(item.id in {"dagster", "nessie"} and item.status != "healthy" for item in services):
        raise BackendUnavailableError("Environment sources are unavailable.")
    runs = await _runs(target.dagster_location)
    connection = uuid4().hex

    async def stream():
        previous_services = {item.id: item.status for item in services}
        previous_runs = runs
        sequence = 0
        deadline = monotonic() + 60
        while monotonic() < deadline:
            if await request.is_disconnected():
                return
            await asyncio.sleep(2)
            try:
                current_services = await _service_snapshots(target)
            except (BackendUnavailableError, BadGatewayError) as exc:
                yield _frame("error", error_envelope(exc))
                return
            for item in current_services:
                if previous_services.get(item.id) != item.status:
                    sequence += 1
                    yield _frame(
                        "service.status",
                        {"env": env, **item.model_dump(mode="json")},
                        f"{connection}:{sequence}",
                    )
            if any(
                item.id in {"dagster", "nessie"} and item.status != "healthy"
                for item in current_services
            ):
                yield _frame(
                    "error",
                    error_envelope(BackendUnavailableError("Environment sources are unavailable.")),
                )
                return
            try:
                current_runs = await _runs(target.dagster_location)
            except (BackendUnavailableError, BadGatewayError) as exc:
                yield _frame("error", error_envelope(exc))
                return
            for run_id, status in current_runs.items():
                if previous_runs.get(run_id) != status:
                    sequence += 1
                    event = RunStatusEvent.model_validate(
                        {
                            "env": env,
                            "run_id": run_id,
                            "status": status,
                            "observed_at": datetime.now(timezone.utc),
                        }
                    )
                    yield _frame(
                        "run.status", event.model_dump(mode="json"), f"{connection}:{sequence}"
                    )
            previous_services = {item.id: item.status for item in current_services}
            previous_runs = current_runs
            yield ": heartbeat\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
