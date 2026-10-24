"""Canonical Observatory routes for the existing audited authoring service."""

from typing import Any

from fastapi import APIRouter, Request

from phlo_api.api import authoring
from phlo_api.api.authoring import CreateWorkflowRequest, PathValidationRequest
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 authoring"])


class WorkflowCreationView(WireModel):
    workflow_type: str
    provider: str
    domain: str
    table: str
    files: list[str]
    next_steps: list[str]
    metadata: dict[str, Any] | None = None


class ValidationView(WireModel):
    valid: bool
    errors: list[str]
    workflow_path: str | None = None
    schema_path: str | None = None


@router.post("/authoring/workflows", response_model=WorkflowCreationView)
def v1_create_workflow(payload: CreateWorkflowRequest, request: Request) -> dict[str, Any]:
    """Create source files with the same scope, rate limit and audit controls as CLI authoring."""
    return authoring.create_workflow(payload, request)


@router.post("/authoring/workflows/validate", response_model=ValidationView)
def v1_validate_workflow(payload: PathValidationRequest, request: Request) -> dict[str, Any]:
    """Validate a project workflow without bypassing project-write scope checks."""
    return authoring.validate_workflow(payload, request)


@router.post("/authoring/schemas/validate", response_model=ValidationView)
def v1_validate_schema(payload: PathValidationRequest, request: Request) -> dict[str, Any]:
    """Validate a project schema using the established path-containment checks."""
    return authoring.validate_schema(payload, request)
