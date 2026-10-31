"""Administrator package installation as a plan-then-apply /api/v1 resource.

The plan resolves the trusted registry entry, the exact install spec and the
currently installed version without running anything. It returns a
confirmation token and the durable journal id that apply will use. Apply
replays an identical idempotent request, otherwise rechecks that state,
claims the operation journal and only then runs the installer.

Installing changes the API process itself, so the action is
installation-wide and takes no environment selector.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from phlo.capabilities.continuity import canonical_json_bytes, sha256_bytes
from phlo.operations.journal import (
    OperationJournalError,
    claim_operation,
    complete_operation,
    mark_submitted,
    mark_unknown,
)
from pydantic import Field

from phlo_api.api.continuity import durable_operation_journal
from phlo_api.api.operation_controls import (
    IdempotencyConflict,
    MutationRejected,
    audit_operation,
    enforce_rate_limit,
    idempotency_key_target,
    replay_or_execute,
    require_scope,
)
from phlo_api.errors import (
    BackendUnavailableError,
    BadGatewayError,
    BadInputError,
    ConflictError,
    PhloApiError,
)
from phlo_api.observatory_api import package_install
from phlo_api.v1_contract import WireModel

router = APIRouter(tags=["v1 packages"])

_ACTION = "package.install"
_AUDIT_OPERATION = "v1_install_package"


class PackageInstallPlan(WireModel):
    """Exact, reviewable package installation the administrator confirms."""

    plan_id: str = Field(min_length=1, max_length=64)
    registry_name: str
    package_name: str
    package_spec: str
    installed_version: str | None


class PackageInstallPlanResponse(WireModel):
    plan: PackageInstallPlan
    plan_token: str
    operation_id: str


class PackageInstallApply(WireModel):
    plan: PackageInstallPlan
    confirmation_token: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=256)


class PackageInstallResult(WireModel):
    operation_id: str
    plan_token: str
    package_name: str
    package_spec: str
    accepted: bool
    status: Literal["succeeded", "failed"]
    message: str
    services: list[str]


def _installed_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def _plan_token(plan: PackageInstallPlan) -> str:
    return sha256_bytes(canonical_json_bytes(plan.model_dump(mode="json")))[:32]


def _operation_id(plan: PackageInstallPlan, plan_token: str) -> str:
    return f"{_ACTION}:{plan.package_name}:{plan_token}"


# Every idempotency claim state that blocks this request maps to one stable code.
_IDEMPOTENCY_CONFLICTS = {
    "target_mismatch": (
        "idempotency_key_conflict",
        "The idempotency key is bound to another plan.",
    ),
    "idempotency_outcome_failed": (
        "idempotency_key_used",
        "This key was rejected before installing. Plan again and use a new key.",
    ),
}
_CLAIM_CONFLICT = (
    "conflicting_claim",
    "Another install of this package is in progress or unresolved.",
)


def _trusted_package(package_name: str) -> package_install.TrustedPackage:
    try:
        return package_install.resolve_trusted_package(package_name)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise BackendUnavailableError(str(exc.detail), code="registry_unavailable") from exc
        raise BadInputError(str(exc.detail), code="untrusted_package") from exc


def _rejected(error: PhloApiError) -> MutationRejected:
    """Mark a rejection proven before the installer starts, releasing the key."""
    return MutationRejected(status_code=error.status_code, detail=error)


@router.post(
    "/admin/packages/{package_name}/install-plan", response_model=PackageInstallPlanResponse
)
def v1_package_install_plan(request: Request, package_name: str) -> PackageInstallPlanResponse:
    """Plan a trusted package installation without running the installer."""
    require_scope(request, "admin")
    package = _trusted_package(package_name)
    plan = PackageInstallPlan(
        plan_id=uuid4().hex,
        registry_name=package.registry_name,
        package_name=package.package_name,
        package_spec=package.package_spec,
        installed_version=_installed_version(package.package_name),
    )
    plan_token = _plan_token(plan)
    return PackageInstallPlanResponse(
        plan=plan, plan_token=plan_token, operation_id=_operation_id(plan, plan_token)
    )


@router.post("/admin/packages/{package_name}/install", response_model=PackageInstallResult)
def v1_package_install(
    request: Request, package_name: str, payload: PackageInstallApply
) -> dict[str, Any]:
    """Apply one confirmed, still-current package installation plan."""
    auth = require_scope(request, "admin")
    enforce_rate_limit(auth["subject"], "install_package")
    plan = payload.plan
    plan_token = _plan_token(plan)
    if payload.confirmation_token != plan_token:
        raise BadInputError(
            "The confirmation token does not match this plan.", code="token_mismatch"
        )
    if package_name not in {
        plan.registry_name,
        plan.package_name,
        plan.package_name.removeprefix("phlo-"),
    }:
        raise BadInputError("The plan is for another package.", code="package_mismatch")
    operation_id = _operation_id(plan, plan_token)
    if idempotency_key_target(payload.idempotency_key, _ACTION) not in {None, operation_id}:
        code, message = _IDEMPOTENCY_CONFLICTS["target_mismatch"]
        raise ConflictError(message, code=code)
    journal = durable_operation_journal()

    def execute() -> dict[str, Any]:
        # Every check here proves the installer has not started, so a
        # rejection releases the idempotency key instead of blocking it.
        try:
            package = _trusted_package(package_name)
        except PhloApiError as exc:
            raise _rejected(exc) from exc
        current = (
            package.registry_name,
            package.package_name,
            package.package_spec,
            _installed_version(package.package_name),
        )
        planned = (plan.registry_name, plan.package_name, plan.package_spec, plan.installed_version)
        if current != planned:
            raise _rejected(
                ConflictError(
                    "The registry entry or installed version changed. Plan again.",
                    code="plan_stale",
                )
            )
        try:
            package_install.package_installer_command(package.package_spec)
        except RuntimeError as exc:
            raise _rejected(
                BackendUnavailableError(str(exc), code="installer_unavailable")
            ) from exc
        try:
            claim_operation(
                journal,
                operation_id=operation_id,
                subject=auth["subject"],
                action=_ACTION,
                target=package.package_name,
                plan_token=plan_token,
            )
        except OperationJournalError as exc:
            raise _rejected(
                ConflictError("This plan was already applied.", code="conflicting_claim")
            ) from exc
        mark_submitted(journal, operation_id)
        try:
            succeeded, message = package_install.run_package_install(package.package_spec)
        except Exception as exc:
            # The installer may have changed the environment; block replay
            # until an operator reconciles the journal entry.
            mark_unknown(journal, operation_id)
            raise BadGatewayError(
                f"The install outcome is unknown; reconcile journal entry {operation_id}.",
                code="apply_outcome_unknown",
            ) from exc
        result = PackageInstallResult(
            operation_id=operation_id,
            plan_token=plan_token,
            package_name=package.package_name,
            package_spec=package.package_spec,
            accepted=succeeded,
            status="succeeded" if succeeded else "failed",
            message=(
                f"Installed {package.package_name}. "
                "Regenerate the Phlo service stack before starting it."
                if succeeded
                else message[-500:]
            ),
            services=(
                package_install.installed_package_services(package)
                if succeeded
                else [package.registry_name]
            ),
        ).model_dump(mode="json")
        complete_operation(journal, operation_id, result)
        return result

    try:
        return replay_or_execute(
            idempotency_key=payload.idempotency_key,
            operation=_ACTION,
            target=operation_id,
            exclusion_target=f"package:{plan.package_name}",
            execute=execute,
            audit=lambda result: audit_operation(
                operation=_AUDIT_OPERATION,
                target=operation_id,
                dry_run=False,
                auth=auth,
                payload=payload.model_dump(mode="json", exclude={"idempotency_key"}),
                result=result,
            ),
        )
    except MutationRejected as exc:
        if isinstance(exc.detail, PhloApiError):
            raise exc.detail from exc
        raise
    except IdempotencyConflict as exc:
        error = exc.detail.get("error") if isinstance(exc.detail, dict) else None
        code, message = _IDEMPOTENCY_CONFLICTS.get(str(error), _CLAIM_CONFLICT)
        raise ConflictError(message, code=code) from exc
