"""Read-only operation preflights for Observatory controls.

Availability combines manifest authorization and local prerequisites, not remote
health or permission to execute. Mutations still enforce resource, ownership,
version, signature and provider checks at the time of the action.
"""

from __future__ import annotations

import asyncio
from importlib import import_module
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request

from phlo.exceptions import PhloCapabilitySetupError
from phlo_api.api.operation_controls import project_root, require_scope
from phlo_api.api.v1 import _target
from phlo_api.api.v1_branch_workflows import _check_job_names, _require_branch_actions
from phlo_api.api.v1_jobs import _require_single_replica_actions
from phlo_api.api.v1_query import _mapped_catalog
from phlo_api.errors import BackendUnavailableError
from phlo_api.observatory_api.dagster import _dagster_operations
from phlo_api.observatory_api.v1_preview import preview_catalog, resolve_trino_url
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation
from phlo_api.v1_contract import Environment, EnvironmentTarget, WireModel

router = APIRouter(tags=["v1 readiness"])
_QUERY = {
    "v1_query_submit",
    "v1_query_explain",
    "v1_query_cancel",
    "v1_saved_query_create",
    "v1_saved_query_update",
    "v1_saved_query_delete",
    "v1_metric_model_preview",
}
_CSV = {
    "v1_csv_upload",
    "v1_csv_upload_review",
    "v1_csv_destination",
    "v1_csv_candidate_plan",
    "v1_csv_candidate_load",
    "v1_csv_candidate_preview",
}
_TRANSFORM = {
    "v1_transform_model_draft_save",
    "v1_transform_model_compile",
    "v1_transform_model_execute",
    "v1_transform_model_execution_preview",
}
_JOBS = {"v1_job_launch", "v1_run_cancel", "v1_run_retry"}
_BRANCHES = {
    "v1_branch_create",
    "v1_branch_delete",
    "v1_branch_rebase",
    "v1_branch_checks",
    "v1_branch_trial_merge",
    "v1_branch_merge",
}
_OPERATIONS = (
    _QUERY
    | _CSV
    | _TRANSFORM
    | _JOBS
    | _BRANCHES
    | {
        "v1_metric_model_create",
        "v1_metric_model_update",
        "v1_data_product_create",
        "v1_data_product_update",
    }
)


class ActionAvailability(WireModel):
    """Report a coarse preflight without granting execution rights."""

    allowed: bool
    reason: str | None


class ActionAvailabilityPage(WireModel):
    """Expose the selected actor's local operation preflights."""

    env: Environment
    actions: dict[str, ActionAvailability]


def _native_preflight(env: Environment, target: EnvironmentTarget) -> None:
    try:
        catalog = preview_catalog(env, target.nessie_ref)
        editor = import_module("phlo_transform.editor")
        editor.preview_settings(
            project_root(),
            ref=target.nessie_ref,
            required_ref=target.nessie_ref,
            required_catalog=catalog,
            required_endpoint=resolve_trino_url(),
            required_user=f"phlo_api_preview_{env}",
        )
    except (
        ImportError,
        AttributeError,
        OSError,
        RuntimeError,
        ValueError,
        PhloCapabilitySetupError,
    ) as exc:
        raise BackendUnavailableError(
            "Native provider, binary or exact preview binding is unavailable. Check native configuration."
        ) from exc


def _local_prerequisites(
    request: Request, env: Environment, target: EnvironmentTarget, operation: str
) -> None:
    if operation in _QUERY:
        _mapped_catalog(request, env)
    if operation in _JOBS | _BRANCHES:
        require_scope(request, "lakehouse:operate")
    if operation in _JOBS:
        _require_single_replica_actions()
    if operation in _BRANCHES:
        _require_branch_actions()
        if operation in {"v1_branch_checks", "v1_branch_trial_merge", "v1_branch_merge"}:
            _check_job_names(env)
    if operation in _CSV:
        import_module("phlo_dlt.csv_upload")
        if operation != "v1_csv_candidate_preview":
            require_scope(request, "project:write")
        if operation in {"v1_csv_destination", "v1_csv_candidate_plan", "v1_csv_candidate_load"}:
            _require_single_replica_actions()
            require_scope(request, "lakehouse:operate")
            import_module("phlo_api.candidate_runtime").preflight_candidate()
        if operation == "v1_csv_candidate_load":
            _dagster_operations()
    if operation in _TRANSFORM:
        import_module("phlo_transform.editor")
        if operation in {"v1_transform_model_draft_save", "v1_transform_model_execute"}:
            require_scope(request, "project:write")
        if operation != "v1_transform_model_draft_save":
            _native_preflight(env, target)
        if operation in {"v1_transform_model_execute", "v1_transform_model_execution_preview"}:
            import_module("phlo_transform.editor_execution")
            import_module("phlo_api.candidate_runtime").preflight_candidate()
        if operation == "v1_transform_model_execute":
            require_scope(request, "lakehouse:operate")


@router.get("/action-availability", response_model=ActionAvailabilityPage)
async def v1_action_availability(
    request: Request,
    env: Environment = Query(),
    job_id: str | None = Query(default=None, min_length=1, max_length=500),
    run_id: str | None = Query(default=None, min_length=1, max_length=500),
    branch_name: str | None = Query(default=None, min_length=1, max_length=128),
) -> ActionAvailabilityPage:
    """Preflight existing controls; allowed is not an execution authorization token."""
    target = _target(
        request, env, allowed_query=frozenset({"env", "job_id", "run_id", "branch_name"})
    )
    # Existing query guards accept only env. Contextual resource IDs are passed
    # to manifest resolution as path identities, just as on the real routes.
    scoped = Request({**request.scope, "query_string": urlencode({"env": env}).encode()})
    context = {
        key: value
        for key, value in {"job_id": job_id, "run_id": run_id, "branch_name": branch_name}.items()
        if value
    }
    actions = {}
    for operation in sorted(_OPERATIONS):
        spec = HTTP_ROUTE_MANIFEST[operation]
        if any(key != "env" and key not in context for key in spec.resource_keys):
            actions[operation] = ActionAvailability(
                allowed=False, reason="Select a job or run first."
            )
            continue
        try:
            await enforce_http_operation(scoped, spec, context)
            await asyncio.to_thread(_local_prerequisites, scoped, env, target, operation)
        except HTTPException as exc:
            reason = (
                "You are not authorized for this action."
                if exc.status_code == 403
                else "Authorization is unavailable."
            )
            actions[operation] = ActionAvailability(allowed=False, reason=reason)
        except BackendUnavailableError as exc:
            actions[operation] = ActionAvailability(allowed=False, reason=exc.message)
        except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
            actions[operation] = ActionAvailability(
                allowed=False, reason="Required local provider or configuration is unavailable."
            )
        else:
            actions[operation] = ActionAvailability(allowed=True, reason=None)
    return ActionAvailabilityPage(env=env, actions=actions)
