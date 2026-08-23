"""Agent-facing authoring API routes.

Routes delegate workflow creation and validation to phlo.workflow_authoring and
the CLI validators, gated by scope checks, rate limits, and operation auditing.
A module-level lock serialises project working-directory changes across requests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from phlo_api.api.operation_controls import (
    audit_operation,
    enforce_rate_limit,
    replay_or_execute,
    require_scope,
)
from phlo_api.pagination import paginate_items

from phlo.cli.commands.doctor import _run_diagnostics_quietly, render_json
from phlo.cli.commands.workflow import (
    _validate_schema_file,
    _validate_workflow_file,
)
from phlo.cli.templates.registry import list_templates as list_project_templates
from phlo.workflow_authoring import WorkflowAuthoringError, create_workflow_with_provider

router = APIRouter(tags=["authoring"])
_project_cwd_lock = Lock()


class CreateWorkflowRequest(BaseModel):
    workflow_type: str = "ingestion"
    domain: str
    table: str
    unique_key: str
    cron: str = "0 */1 * * *"
    api_base_url: str | None = None
    fields: list[str] = Field(default_factory=list)
    provider: str | None = None
    contribution_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class PathValidationRequest(BaseModel):
    path: str | None = None
    workflow_path: str | None = None
    schema_path: str | None = None


def _project_root() -> Path:
    return Path(os.environ.get("PHLO_PROJECT_PATH", ".")).resolve()


def _resolve_project_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (_project_root() / candidate).resolve()
    project_root = _project_root()
    if resolved != project_root and project_root not in resolved.parents:
        raise HTTPException(
            status_code=400,
            detail={"error": "path_outside_project", "project_root": str(project_root)},
        )
    return resolved


@contextmanager
def _project_cwd() -> Iterator[None]:
    """Enter the project root for code that resolves relative paths against it.

    os.chdir is process-global, so the lock serializes concurrent requests.
    """
    with _project_cwd_lock:
        previous = Path.cwd()
        os.chdir(_project_root())
        try:
            yield
        finally:
            os.chdir(previous)


@router.post("/workflows")
def create_workflow(request: CreateWorkflowRequest, http_request: Request) -> dict[str, Any]:
    """Create a workflow scaffold in the current project."""
    auth = require_scope(http_request, "project:write")
    enforce_rate_limit(auth["subject"], "create_workflow")
    try:
        result = create_workflow_with_provider(
            project_root=_project_root(),
            workflow_type=request.workflow_type,
            domain=request.domain,
            table=request.table,
            unique_key=request.unique_key,
            cron=request.cron,
            api_base_url=request.api_base_url,
            fields=request.fields,
            provider=request.provider,
            contribution_id=request.contribution_id,
            values=request.values,
        )
    except FileExistsError as exc:
        payload = {
            "error": "workflow_already_exists",
            "message": str(exc),
            "target": f"{request.domain}/{request.table}",
        }
        audit_operation(
            operation="create_workflow",
            target=f"{request.domain}/{request.table}",
            dry_run=False,
            auth=auth,
            payload=request.model_dump(mode="json"),
            result=payload,
        )
        raise HTTPException(status_code=409, detail=payload) from exc
    except WorkflowAuthoringError as exc:
        payload = {
            "error": "workflow_authoring_unavailable",
            "message": str(exc),
            "target": f"{request.domain}/{request.table}",
        }
        audit_operation(
            operation="create_workflow",
            target=f"{request.domain}/{request.table}",
            dry_run=False,
            auth=auth,
            payload=request.model_dump(mode="json"),
            result=payload,
        )
        raise HTTPException(status_code=422, detail=payload) from exc
    payload = {
        "workflow_type": result.workflow_type,
        "provider": result.provider,
        "domain": result.domain,
        "table": result.table,
        "files": result.files,
        "next_steps": result.next_steps,
        "metadata": result.metadata,
    }
    return replay_or_execute(
        idempotency_key=None,
        operation="create_workflow",
        target=f"{request.domain}/{request.table}",
        execute=lambda: payload,
        audit=lambda response: audit_operation(
            operation="create_workflow",
            target=f"{request.domain}/{request.table}",
            dry_run=False,
            auth=auth,
            payload=request.model_dump(mode="json"),
            result=response,
        ),
    )


@router.post("/workflows/validate")
def validate_workflow(request: PathValidationRequest, http_request: Request) -> dict[str, Any]:
    """Validate a workflow file."""
    auth = require_scope(http_request, "project:write")
    enforce_rate_limit(auth["subject"], "validate_workflow")
    workflow_path = request.workflow_path or request.path
    if not workflow_path:
        return {"valid": False, "errors": ["workflow_path is required"]}
    try:
        resolved = _resolve_project_path(workflow_path)
        _validate_workflow_file(str(resolved))
        payload = {"valid": True, "workflow_path": str(resolved), "errors": []}
    except Exception as exc:
        payload = {"valid": False, "workflow_path": workflow_path, "errors": [str(exc)]}
    audit_operation(
        operation="validate_workflow",
        target=workflow_path,
        dry_run=True,
        auth=auth,
        payload=request.model_dump(mode="json"),
        result=payload,
    )
    return payload


@router.post("/schemas/validate")
def validate_schema(request: PathValidationRequest, http_request: Request) -> dict[str, Any]:
    """Validate a schema file."""
    auth = require_scope(http_request, "project:write")
    enforce_rate_limit(auth["subject"], "validate_schema")
    schema_path = request.schema_path or request.path
    if not schema_path:
        return {"valid": False, "errors": ["schema_path is required"]}
    try:
        resolved = _resolve_project_path(schema_path)
        _validate_schema_file(str(resolved))
        payload = {"valid": True, "schema_path": str(resolved), "errors": []}
    except Exception as exc:
        payload = {"valid": False, "schema_path": schema_path, "errors": [str(exc)]}
    audit_operation(
        operation="validate_schema",
        target=schema_path,
        dry_run=True,
        auth=auth,
        payload=request.model_dump(mode="json"),
        result=payload,
    )
    return payload


@router.get("/templates")
def list_templates() -> dict[str, Any]:
    """List project starter templates."""
    return {
        "items": [
            {
                "name": template.metadata.name,
                "description": template.metadata.description,
                "required_packages": list(template.metadata.required_packages),
                "generated_paths": list(template.metadata.generated_paths),
                "next_steps": list(template.metadata.next_steps),
            }
            for template in list_project_templates()
        ]
    }


@router.get("/workflows")
def list_workflows(
    search: str | None = None,
    group: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List workflow Python files discovered in the project."""
    workflows_root = _project_root() / "workflows"
    items: list[dict[str, Any]] = []
    if workflows_root.exists():
        for path in sorted(workflows_root.rglob("*.py")):
            relative = path.relative_to(_project_root())
            parts = relative.parts
            workflow_group = parts[1] if len(parts) > 2 else None
            item = {"path": str(relative), "name": path.stem, "group": workflow_group}
            haystack = f"{item['path']} {item['name']} {item['group'] or ''}".lower()
            if search and search.lower() not in haystack:
                continue
            if group and workflow_group != group:
                continue
            items.append(item)
    page, next_cursor = paginate_items(items, limit=limit, cursor=cursor)
    return {"items": page, "next_cursor": next_cursor}


@router.post("/project/lint")
def lint_project(http_request: Request) -> dict[str, Any]:
    """Run lightweight project lint checks for agents."""
    auth = require_scope(http_request, "project:write")
    enforce_rate_limit(auth["subject"], "lint_project")
    doctor = run_doctor()
    workflows = list_workflows().get("items", [])
    payload = {
        "ok": doctor["summary"].get("fail", 0) == 0,
        "doctor": doctor,
        "workflow_count": len(workflows),
        "warnings": [],
        "errors": [] if doctor["summary"].get("fail", 0) == 0 else ["doctor reported failures"],
    }
    audit_operation(
        operation="lint_project",
        target=str(_project_root()),
        dry_run=True,
        auth=auth,
        result=payload,
    )
    return payload


@router.get("/doctor")
def run_doctor(verbose: bool = False) -> dict[str, Any]:
    """Run the same diagnostic engine as `phlo doctor --json`."""
    with _project_cwd():
        return json.loads(render_json(_run_diagnostics_quietly(verbose=verbose)))
