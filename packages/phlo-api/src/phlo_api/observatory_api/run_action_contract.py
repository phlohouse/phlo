"""Provider-neutral run-action contract for guarded retry/cancel endpoints.

One typed result names the exact run, capability, permission, risk,
confirmation, idempotency, outcome, and canonical evidence identity for a
pipeline run action. The guarded endpoints normalize every provider reply
into :class:`RunActionResult` before persisting it, so a replayed idempotent
response is byte-identical to the original and never re-invokes the provider.

Canonical report identity (``project_id/run_id/attempt``) is attached only
when the durable run-evidence store already holds a record for the resulting
run; it is never inferred from provider payloads or display names.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from phlo_api.observatory_api.observatory_models import (
    ObservatoryAction,
    ObservatoryRunReportIdentity,
)

RUN_ACTION_CONTRACT_VERSION = 1

RunActionKind = Literal["run.retry", "run.cancel"]
RunActionStatus = Literal["accepted", "pending", "reconciled", "rejected", "skipped"]

_OPERATION_FOR_KIND: dict[str, str] = {
    "run.retry": "retry_failed_run",
    "run.cancel": "cancel_run",
}


class RunActionIdentity(BaseModel):
    """Exact provider run identity targeted or produced by a run action."""

    run_id: str
    project_id: str | None = None
    attempt: int | None = None


class RunActionContract(BaseModel):
    """Typed guard metadata shared by the read model and the guarded endpoints."""

    action_id: str
    kind: RunActionKind
    label: str
    required_capability: str
    required_permission: str
    risk_level: Literal["low", "medium", "high", "critical"]
    requires_confirmation: bool
    expected_evidence: tuple[str, ...]

    def reason_for(self, *, enabled: bool) -> str | None:
        """Return the read-model reason string shown when the action is disabled."""
        if enabled:
            return None
        if self.kind == "run.retry":
            return "Retry is available only for failed runs."
        return "Cancel is available only for running runs."


RETRY_RUN_ACTION = RunActionContract(
    action_id="retry",
    kind="run.retry",
    label="Retry",
    required_capability="orchestrator_operations",
    required_permission="lakehouse:operate",
    risk_level="high",
    requires_confirmation=True,
    expected_evidence=(
        "run.retry.verification_handle",
        "run.retry.resulting_run_identity",
        "canonical report identity project_id/run_id/attempt",
    ),
)

CANCEL_RUN_ACTION = RunActionContract(
    action_id="cancel",
    kind="run.cancel",
    label="Cancel",
    required_capability="orchestrator_operations",
    required_permission="lakehouse:operate",
    risk_level="medium",
    requires_confirmation=True,
    expected_evidence=(
        "run.cancel.verification_handle",
        "run.cancel.terminal_run_status",
        "canonical report identity project_id/run_id/attempt",
    ),
)


def observatory_action(
    contract: RunActionContract, *, enabled: bool, run_id: str | None
) -> ObservatoryAction:
    """Render one contract as the read-model action descriptor."""
    return ObservatoryAction(
        id=contract.action_id,
        label=contract.label,
        kind=contract.kind,
        enabled=enabled,
        requires_confirmation=contract.requires_confirmation,
        reason=contract.reason_for(enabled=enabled),
        risk_level=contract.risk_level,
        required_capability=contract.required_capability,
        required_permission=contract.required_permission,
        expected_evidence=list(contract.expected_evidence),
        background_operation_id=run_id,
    )


class RunActionResult(BaseModel):
    """One provider-neutral result for one guarded run action.

    ``status`` distinguishes accepted (provider launched or accepted the
    action), pending (provider claimed success but did not name a distinct
    resulting run, so the outcome is ambiguous), reconciled (durable evidence
    resolved the canonical run/report identity), rejected (provider
    authoritatively refused), and skipped (dry run: nothing executed). Every
    result carries the same durable ``verification_handle`` on replay.
    """

    contract_version: int = RUN_ACTION_CONTRACT_VERSION
    action_kind: RunActionKind
    status: RunActionStatus
    verification_handle: str
    target: RunActionIdentity
    resulting_run: RunActionIdentity | None = None
    canonical_report: ObservatoryRunReportIdentity | None = None
    canonical_report_path: str | None = None
    provider: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


def run_action_verification_handle(
    *, action_kind: str, target_run_id: str, idempotency_key: str
) -> str:
    """Derive the durable verification handle for one guarded run action.

    The handle is deterministic in (operation, target run, idempotency key),
    so an idempotent replay always names the identical handle whether it is
    answered from the persisted claim store or recomputed.
    """
    operation = _OPERATION_FOR_KIND.get(action_kind, action_kind)
    digest = hashlib.sha256(
        f"{operation}|{target_run_id}|{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"vh-{digest[:32]}"


def require_idempotency_key(idempotency_key: str | None) -> str:
    """Reject missing or blank idempotency keys before any provider invocation."""
    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "idempotency_key_required",
                "message": "Run actions require a non-blank idempotency key.",
            },
        )
    return idempotency_key


def _provider_payload(provider_result: Any) -> dict[str, Any]:
    if isinstance(provider_result, dict):
        return dict(provider_result)
    if hasattr(provider_result, "model_dump"):
        return dict(provider_result.model_dump(mode="json"))
    if hasattr(provider_result, "to_dict"):
        return dict(provider_result.to_dict())
    return {"result": provider_result}


def _resulting_run_identity(
    action_kind: str, target_run_id: str, payload: dict[str, Any]
) -> RunActionIdentity | None:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    if action_kind == "run.cancel":
        return RunActionIdentity(run_id=target_run_id)
    if run_id == target_run_id:
        # The provider echoed the retried run instead of naming a new one;
        # whether a distinct re-execution launched stays ambiguous.
        return None
    return RunActionIdentity(run_id=run_id)


def normalize_run_action_result(
    *,
    action_kind: RunActionKind,
    target_run_id: str,
    provider_result: Any,
    idempotency_key: str,
    message: str | None = None,
) -> RunActionResult:
    """Normalize one provider reply into the neutral run-action result.

    Classification is provider-neutral: ``dry_run`` replies are skipped (never
    executed), ``accepted=False`` replies are rejected, accepted replies that
    name a distinct resulting run are accepted, and accepted replies without a
    distinct resulting run stay pending so a later reconciliation resolves the
    canonical identity. Nothing here re-invokes the provider.
    """
    payload = _provider_payload(provider_result)
    resulting_run: RunActionIdentity | None = None
    if payload.get("dry_run"):
        status: RunActionStatus = "skipped"
    elif not payload.get("accepted"):
        status = "rejected"
    else:
        resulting_run = _resulting_run_identity(action_kind, target_run_id, payload)
        status = "accepted" if resulting_run is not None else "pending"
    result_message = message if message is not None else str(payload.get("message") or "")
    return RunActionResult(
        action_kind=action_kind,
        status=status,
        verification_handle=run_action_verification_handle(
            action_kind=action_kind,
            target_run_id=target_run_id,
            idempotency_key=idempotency_key,
        ),
        target=RunActionIdentity(run_id=target_run_id),
        resulting_run=resulting_run,
        provider=payload,
        message=result_message,
    )


def resolve_run_action_reconciliation(
    result: RunActionResult, store: Any, *, project_id: str | None = None
) -> RunActionResult:
    """Attach canonical run/report identity when durable evidence proves it.

    The canonical ``project_id/run_id/attempt`` identity is taken only from a
    durable run-evidence record for the resulting run; without such a
    record the result is returned unchanged and stays ``accepted``/``pending``.
    """
    if result.canonical_report is not None or project_id is None:
        return result
    run = result.resulting_run
    if run is None:
        return result
    row = store.get_run(project_id, run.run_id)
    if row is None:
        return result
    attempt = row.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        return result
    identity = ObservatoryRunReportIdentity(
        project_id=project_id, run_id=run.run_id, attempt=attempt
    )
    return result.model_copy(
        update={
            "status": "reconciled",
            "canonical_report": identity,
            "canonical_report_path": (
                f"/api/observatory/projects/{identity.project_id}"
                f"/runs/{identity.run_id}/attempts/{identity.attempt}/report"
            ),
        }
    )
