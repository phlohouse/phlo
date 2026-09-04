"""Guarded continuity action API.

HTTP projection of neutral continuity contracts: plan-first maintenance,
verified backup sets, explicit-target restore, and supported version upgrade.
Every endpoint maps 1:1 to exactly one landed core service; this adapter adds
no provider behavior, invokes no CLI process, and never widens the provider
deletion/restore boundary.

The vocabulary is explain > confirm > act > verify:

- ``GET  /operations`` — read-only inventory of the supported operations, one
  per landed family. Destructive orphan deletion is listed as explicitly
  unsupported: no bounded deletion set exists.
- ``POST /plan`` — read-only, mutation-free dry-run planning that returns an
  immutable, target-bound plan (deterministic token for identical inputs).
- ``POST /apply`` — one guarded apply endpoint: authenticated, authorized,
  confirmed, bounded to the landed operation allow-list, audited, and
  durable-idempotent through the operation journal before any provider
  invocation.
- ``GET  /verifications/{operation_id}`` — canonical verification lookup of
  the durable journal entry; restart-safe. An unknown post-submission outcome
  is reported as such and blocks new-key replay.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from phlo.capabilities.continuity import (
    BACKUP_PROVIDER_ORDER,
    RestoreTarget,
    canonical_json_bytes,
    sha256_bytes,
)
from phlo.operations.backup import create_backup_set, default_backup_contributors
from phlo.operations.journal import (
    OperationJournalError,
    OperationJournalState,
    OperationJournalStore,
    claim_operation,
    complete_operation,
    mark_submitted,
    mark_unknown,
)
from phlo.operations.journal_store import FileOperationJournalStore
from phlo.operations.restore import (
    RestoreError,
    RestorePlan,
    plan_restore,
    restore_apply,
    restore_operation_id,
)
from phlo.operations.upgrade import (
    UpgradeError,
    UpgradePlan,
    plan_upgrade,
    upgrade_apply,
    upgrade_operation_id,
)
from phlo_api.api.operation_controls import (
    audit_operation,
    enforce_rate_limit,
    idempotency_key_target,
    require_scope,
    replay_or_execute,
)

router = APIRouter(tags=["continuity"])

JOURNAL_DIR_ENV = "PHLO_OPERATIONS_JOURNAL_DIR"

# The bounded, one-to-one operation surface. Each entry wraps exactly one
# landed core service; nothing here may invoke providers directly.
_PLAN_OPERATIONS = ("restore.plan", "upgrade.plan", "maintenance.plan")
_APPLY_OPERATIONS = ("backup.create", "restore.apply", "upgrade.apply", "maintenance.apply")
_MAINTENANCE_OPERATIONS = ("compact", "snapshot_expiry")

# Destructive orphan deletion stays unsupported: no bounded deletion set
# exists, so it is never claimed, planned, or applied through any surface.
_UNSUPPORTED_OPERATIONS = (
    {
        "operation": "orphan_delete",
        "family": "maintenance",
        "reason": "no bounded deletion set exists; destructive orphan deletion is unsupported",
    },
)

_INVENTORY_OPERATIONS = (
    {
        "operation": "maintenance.plan",
        "family": "maintenance",
        "surface": "plan",
        "requires_confirmation": False,
        "description": "Deterministic, mutation-free maintenance plan for one table.",
    },
    {
        "operation": "maintenance.apply",
        "family": "maintenance",
        "surface": "apply",
        "requires_confirmation": True,
        "description": "Apply an exact, still-current maintenance plan (authorized, journaled).",
    },
    {
        "operation": "backup.create",
        "family": "backup",
        "surface": "apply",
        "requires_confirmation": False,
        "description": "Create one immutable, verified backup set (authorized, journaled).",
    },
    {
        "operation": "restore.plan",
        "family": "restore",
        "surface": "plan",
        "requires_confirmation": False,
        "description": "Mutation-free restore plan bound to a verified set digest and explicit target.",
    },
    {
        "operation": "restore.apply",
        "family": "restore",
        "surface": "apply",
        "requires_confirmation": True,
        "description": "Apply a restore plan only to its bound target (authorized, journaled).",
    },
    {
        "operation": "upgrade.plan",
        "family": "upgrade",
        "surface": "plan",
        "requires_confirmation": False,
        "description": "Mutation-free upgrade plan for the one supported version pair.",
    },
    {
        "operation": "upgrade.apply",
        "family": "upgrade",
        "surface": "apply",
        "requires_confirmation": True,
        "description": "Apply the bound upgrade after a verified backup (authorized, journaled).",
    },
)


class ContinuityPlanRequest(BaseModel):
    """Dry-run planning request for one plan-bearing continuity operation."""

    operation: str
    backup_set: str | None = None
    target: str | None = None
    from_version: str | None = None
    to_version: str | None = None
    maintenance_operation: str | None = None
    table: str | None = None
    ref: str = "main"


class ContinuityApplyRequest(BaseModel):
    """Guarded apply request for one continuity operation."""

    operation: str
    confirmation_token: str | None = None
    idempotency_key: str | None = None
    plan: dict[str, Any] | None = None
    target: str | None = None
    table: str | None = None
    ref: str = "main"


# --- shared guards ----------------------------------------------------------


def _durable_journal() -> FileOperationJournalStore:
    """Resolve the configured durable journal; fail closed when none is present.

    An authorized mutation must never silently fall back to an ephemeral
    in-memory journal, or the exactly-once contract disappears with the
    process. This is the same store the CLI resolves from the same environment
    variable, so both surfaces share one canonical verification handle.
    """
    directory = os.environ.get(JOURNAL_DIR_ENV)
    if not directory:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "journal_unconfigured",
                "reason": "durable operation journal not configured",
            },
        )
    return FileOperationJournalStore(directory)


def _deterministic_plan_token(*parts: str) -> str:
    """Derive an immutable plan token from the plan's bound inputs."""
    return sha256_bytes(canonical_json_bytes(list(parts)))[:32]


def _error(status_code: int, code: str, identifiers: tuple[str, ...] = ()) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"error": code, "identifiers": list(identifiers)}
    )


_CONFLICT_CODES = frozenset(
    {"conflicting_claim", "unknown_outcome_blocks_replay", "unknown_operation"}
)


def _continuity_error(exc: RestoreError | UpgradeError | OperationJournalError) -> HTTPException:
    """Map a stable core contract violation onto an HTTP status, pre-invocation."""
    if exc.code in _CONFLICT_CODES:
        return _error(409, exc.code, exc.identifiers)
    return _error(400, exc.code, exc.identifiers)


def _reject_unsupported(operation: str) -> HTTPException:
    if operation == "orphan_delete":
        return _error(400, "orphan_delete_unsupported", (operation,))
    return _error(400, "unsupported_operation", (operation,))


def _require_confirmation(request_confirmation: str | None, plan_token: str) -> None:
    """Reject a wrong or missing confirmation before any provider invocation."""
    if not request_confirmation or request_confirmation != plan_token:
        raise _error(400, "token_mismatch", (plan_token,))


def _parsed_plan(plan: dict[str, Any] | None, cls: type[RestorePlan] | type[UpgradePlan]) -> Any:
    if not plan:
        raise _error(400, "missing_plan")
    try:
        return cls.from_dict(plan)
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(400, "invalid_plan", (type(exc).__name__,)) from exc


def _maintenance_executor(operation: str) -> Any:
    from phlo.capabilities import resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    resolution = resolve_capability("maintenance_executor", operation)
    if resolution is None:
        raise _error(400, "unknown_maintenance_operation", (operation,))
    execute_fn = getattr(resolution.provider, "execute", None)
    if not callable(execute_fn):
        raise _error(400, "maintenance_executor_cannot_execute", (operation,))
    return resolution.provider


# --- inventory --------------------------------------------------------------


@router.get("/operations")
def list_continuity_operations(http_request: Request) -> dict[str, Any]:
    """List the supported continuity operations, one per landed family (read-only)."""
    require_scope(http_request, "lakehouse:read")
    return {
        "operations": list(_INVENTORY_OPERATIONS),
        "unsupported": list(_UNSUPPORTED_OPERATIONS),
    }


# --- planning ---------------------------------------------------------------


@router.post("/plan")
def post_continuity_plan(request: ContinuityPlanRequest, http_request: Request) -> dict[str, Any]:
    """Return an immutable, target-bound dry-run plan (read-only, no mutation)."""
    require_scope(http_request, "lakehouse:read")
    try:
        return _plan_operation(request)
    except (RestoreError, UpgradeError, OperationJournalError) as exc:
        raise _continuity_error(exc) from exc


def _plan_operation(request: ContinuityPlanRequest) -> dict[str, Any]:
    operation = request.operation

    if operation == "restore.plan":
        if not request.backup_set or not request.target:
            raise _error(400, "missing_field", ("backup_set,target",))
        plan = plan_restore(
            backup_set_dir=Path(request.backup_set),
            target=RestoreTarget.of(request.target),
            plan_token=_deterministic_plan_token(
                "restore.apply",
                str(Path(request.backup_set).resolve()),
                str(Path(request.target).resolve()),
            ),
        )
        return {
            "operation": operation,
            "plan": plan.to_dict(),
            "plan_token": plan.plan_token,
            "operation_id": restore_operation_id(plan),
        }

    if operation == "upgrade.plan":
        if not request.backup_set or not request.target:
            raise _error(400, "missing_field", ("backup_set,target",))
        if not request.from_version or not request.to_version:
            raise _error(400, "missing_field", ("from_version,to_version",))
        plan = plan_upgrade(
            from_version=request.from_version,
            to_version=request.to_version,
            backup_set_dir=Path(request.backup_set),
            target=RestoreTarget.of(request.target),
            plan_token=_deterministic_plan_token(
                "upgrade.apply",
                request.from_version,
                request.to_version,
                str(Path(request.backup_set).resolve()),
                str(Path(request.target).resolve()),
            ),
        )
        return {
            "operation": operation,
            "plan": plan.to_dict(),
            "plan_token": plan.plan_token,
            "operation_id": upgrade_operation_id(plan),
        }

    if operation == "maintenance.plan":
        maintenance_operation = request.maintenance_operation or ""
        if maintenance_operation not in _MAINTENANCE_OPERATIONS:
            raise _error(400, "unknown_maintenance_operation", (maintenance_operation,))
        if not request.table:
            raise _error(400, "missing_field", ("table",))
        provider = _maintenance_executor(maintenance_operation)
        plan_fn = getattr(provider, "plan", None)
        if not callable(plan_fn):
            raise _error(400, "maintenance_executor_cannot_plan", (maintenance_operation,))
        plan = plan_fn(table_name=request.table, ref=request.ref)
        return {
            "operation": operation,
            "plan": plan,
            "plan_token": plan.get("plan_token", ""),
            "operation_id": f"{maintenance_operation}:{request.table}:{request.ref}",
        }

    raise _reject_unsupported(operation)


# --- guarded apply ----------------------------------------------------------


def _restore_execution(
    request: ContinuityApplyRequest, journal: OperationJournalStore, subject: str
) -> tuple[str, str, Any]:
    plan = _parsed_plan(request.plan, RestorePlan)
    _require_confirmation(request.confirmation_token, plan.plan_token)
    plan_id = restore_operation_id(plan)

    def execute() -> dict[str, Any]:
        result = restore_apply(
            plan=plan,
            confirmation_token=str(request.confirmation_token),
            contributors=dict(default_backup_contributors()),
            journal=journal,
            subject=subject,
        )
        return {"operation": "restore.apply", "operation_id": plan_id, **result.to_dict()}

    return plan.plan_token, plan_id, execute


def _upgrade_execution(
    request: ContinuityApplyRequest, journal: OperationJournalStore, subject: str
) -> tuple[str, str, Any]:
    plan = _parsed_plan(request.plan, UpgradePlan)
    _require_confirmation(request.confirmation_token, plan.plan_token)
    plan_id = upgrade_operation_id(plan)

    def execute() -> dict[str, Any]:
        result = upgrade_apply(
            plan=plan,
            confirmation_token=str(request.confirmation_token),
            contributors=dict(default_backup_contributors()),
            journal=journal,
            subject=subject,
        )
        return {"operation": "upgrade.apply", "operation_id": plan_id, **result.to_dict()}

    return plan.plan_token, plan_id, execute


def _backup_execution(
    request: ContinuityApplyRequest, journal: OperationJournalStore, subject: str
) -> tuple[str, str, Any]:
    if not request.target:
        raise _error(400, "missing_field", ("target",))
    target = str(Path(request.target).resolve())

    def execute() -> dict[str, Any]:
        try:
            contributors = default_backup_contributors()
        except LookupError as exc:
            raise _error(503, "contributors_unavailable", (str(exc),)) from exc
        contributors = sorted(contributors, key=lambda item: BACKUP_PROVIDER_ORDER.index(item[0]))
        result = create_backup_set(
            target=Path(target),
            contributors=contributors,
            journal=journal,
            subject=subject,
        )
        return {
            "operation": "backup.create",
            "operation_id": f"backup.create:{result.set_id}",
            **result.to_dict(),
        }

    return target, "", execute


def _maintenance_execution(
    request: ContinuityApplyRequest, journal: OperationJournalStore, subject: str
) -> tuple[str, str, Any]:
    plan = request.plan or {}
    maintenance_operation = str(plan.get("operation", ""))
    if maintenance_operation not in _MAINTENANCE_OPERATIONS:
        raise _error(400, "unknown_maintenance_operation", (maintenance_operation,))
    table = request.table or str(plan.get("table_name", ""))
    ref = request.ref or str(plan.get("ref", "main"))
    plan_token = str(plan.get("plan_token", ""))
    _require_confirmation(request.confirmation_token, plan_token)
    plan_id = f"{maintenance_operation}:{table}:{ref}"

    def execute() -> dict[str, Any]:
        provider = _maintenance_executor(maintenance_operation)
        # Claim the durable journal exactly like the CLI surface.
        claim_operation(
            journal,
            operation_id=plan_id,
            subject=subject,
            action=maintenance_operation,
            target=table,
            plan_token=plan_token,
        )
        mark_submitted(journal, plan_id)
        try:
            result = provider.execute(table_name=table, ref=ref, plan_token=plan_token)
            result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        except Exception as exc:
            # Submitted but the outcome is unknown: record UNKNOWN so no new
            # key can replay this operation automatically.
            mark_unknown(journal, plan_id)
            raise _error(502, "apply_outcome_unknown", (plan_id,)) from exc
        complete_operation(journal, plan_id, result_dict)
        return {"operation": "maintenance.apply", "operation_id": plan_id, **result_dict}

    return plan_token, plan_id, execute


@router.post("/apply")
def post_continuity_apply(request: ContinuityApplyRequest, http_request: Request) -> dict[str, Any]:
    """Apply one confirmed, authorized, idempotent continuity operation.

    Every guard runs before any provider invocation: operation allow-list,
    confirmation-token match, idempotency-key binding, and the durable journal
    claim inside the core service itself.
    """
    auth = require_scope(http_request, "lakehouse:operate")
    enforce_rate_limit(auth["subject"], "continuity_apply")
    operation = request.operation

    if operation not in _APPLY_OPERATIONS:
        raise _reject_unsupported(operation)
    if not request.idempotency_key:
        raise _error(400, "missing_idempotency_key")

    journal = _durable_journal()
    execution = {
        "restore.apply": _restore_execution,
        "upgrade.apply": _upgrade_execution,
        "backup.create": _backup_execution,
        "maintenance.apply": _maintenance_execution,
    }[operation]
    target, _plan_id, execute = execution(request, journal, auth["subject"])

    existing = idempotency_key_target(request.idempotency_key, operation)
    if existing is not None and existing != target:
        raise _error(409, "idempotency_key_conflict", (existing, target))

    try:
        return replay_or_execute(
            idempotency_key=request.idempotency_key,
            operation=operation,
            target=target,
            execute=execute,
            audit=lambda result: audit_operation(
                operation=operation,
                target=target,
                dry_run=False,
                auth=auth,
                payload=request.model_dump(mode="json"),
                result=result,
            ),
        )
    except OperationJournalError as exc:
        raise _continuity_error(exc) from exc
    except (RestoreError, UpgradeError) as exc:
        raise _continuity_error(exc) from exc


# --- canonical verification -------------------------------------------------


@router.get("/verifications/{operation_id:path}")
def get_continuity_verification(operation_id: str, http_request: Request) -> dict[str, Any]:
    """Resolve one durable evidence handle to its canonical journal state.

    Restart-safe: the handle reads the durable operation journal, so the result
    survives process restarts. An ``unknown`` post-submission outcome is
    reported as such and can never be automatically replayed.
    """
    require_scope(http_request, "lakehouse:read")
    journal: OperationJournalStore = _durable_journal()
    entry = journal.read(operation_id)
    if entry is None:
        raise _error(404, "unknown_operation", (operation_id,))
    payload = entry.to_dict()
    payload["replay_blocked"] = entry.state is OperationJournalState.UNKNOWN
    return payload
