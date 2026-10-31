"""Project-scoped operation history and agent investigation context.

Operations come from the project's operation journal, recorded WAP reports,
lakehouse manifests, and maintenance read models. None of these sources carry
an environment, so the resources are project-scoped and reject ``env``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from phlo_api.api.v1_pages import decode_offset, encode_cursor, reject_unknown_query
from phlo_api.errors import BackendUnavailableError, NotFoundError
from phlo_api.observatory_api import observatory
from phlo_api.observatory_api.observatory_models import (
    ObservatoryAction,
    ObservatoryHealth,
    ObservatoryLogEvent,
    ObservatoryOperation,
    ObservatoryResourceRef,
    OperationStatus,
)
from phlo_api.observatory_api.observatory_operation_journal import (
    build_operation_observability_context,
)
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 operations"])
_LIST_QUERY = frozenset({"status", "kind", "q", "limit", "cursor"})


class OperationPage(BaseModel):
    scope: Literal["project"] = "project"
    items: list[ObservatoryOperation]
    next_cursor: str | None


class OperationSummary(BaseModel):
    id: str
    name: str
    kind: str
    status: OperationStatus
    health: ObservatoryHealth
    target: ObservatoryResourceRef | None
    started_at: str | None
    completed_at: str | None
    duration_seconds: float | None


class OperationIdentifiers(WireModel):
    operation_id: str
    trace_ids: list[str]
    log_ids: list[str]
    metric_ids: list[str]
    incident_ids: list[str]


class OperationIncident(BaseModel):
    status: Literal["open", "resolved"]
    severity: str
    message: str | None
    incident_ids: list[str]


class OperationRetention(WireModel):
    history_limit: int
    history_store: str


class OperationAgentContext(BaseModel):
    schema_version: Literal["phlo.operation_observability.v1"]
    operation: OperationSummary
    identifiers: OperationIdentifiers
    incident: OperationIncident
    retention: OperationRetention
    metadata: dict[str, Any]
    related: list[ObservatoryResourceRef]
    logs: list[ObservatoryLogEvent]
    actions: list[ObservatoryAction]


def _operations() -> list[ObservatoryOperation]:
    try:
        return observatory._load_operations()
    except RuntimeError as exc:
        raise BackendUnavailableError("Operation history storage is unavailable.") from exc


@router.get("/operations", response_model=OperationPage)
def v1_operations(
    request: Request,
    status: OperationStatus | None = None,
    kind: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> OperationPage:
    """List operations, newest first, filtered before pagination.

    ``status`` and ``kind`` match exactly (case-insensitive); ``q`` matches a
    case-insensitive substring of the serialized operation. The cursor is an
    offset bound to the filters; operations recorded between requests can
    shift later pages.
    """
    reject_unknown_query(request, _LIST_QUERY)
    scope = ["operations", status, kind, q]
    offset = decode_offset(cursor, scope)
    matches = observatory._filter_operations(_operations(), status=status, kind=kind, q=q)
    page = matches[offset : offset + limit]
    following = offset + limit
    return OperationPage(
        items=page,
        next_cursor=encode_cursor(scope, following) if following < len(matches) else None,
    )


@router.get("/operations/{operation_id:path}/agent-context", response_model=OperationAgentContext)
def v1_operation_agent_context(request: Request, operation_id: str) -> OperationAgentContext:
    """Return the versioned observability context an agent needs to investigate one operation."""
    reject_unknown_query(request, frozenset())
    try:
        detail = observatory._load_operation_detail(operation_id)
    except RuntimeError as exc:
        raise BackendUnavailableError("Operation history storage is unavailable.") from exc
    except HTTPException as exc:
        if exc.status_code == 404:
            raise NotFoundError("Operation was not found.") from exc
        raise
    context = build_operation_observability_context(detail.operation)
    return OperationAgentContext.model_validate(
        {
            **context,
            "related": detail.related,
            "logs": detail.logs,
            "actions": detail.actions,
        }
    )
