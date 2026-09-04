"""Restore planning and apply (ADR 0049 §4, Plan 012).

``plan_restore`` is mutation-free and binds a plan to the verified set
digest and an explicitly confirmed target. ``restore_apply`` revalidates
every field, reverifies the set, claims the Plan 010 journal, restores
providers in the reverse order, and returns an evidence-based
reconciliation verdict — never service health or table existence alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from phlo.capabilities.continuity import (
    BACKUP_PROVIDER_ORDER,
    RESTORE_PLAN_SCHEMA_VERSION,
    RESTORE_PLAN_TTL_SECONDS,
    RESTORE_PROVIDER_ORDER,
    BackupArtifact,
    BackupContributorState,
    BackupSetError,
    BackupSetManifest,
    RestoreContributor,
    RestorePlan,
    RestoreReconciliationResult,
    RestoreResult,
    RestoreStepResult,
    RestoreTarget,
    canonical_json_bytes,
    sha256_bytes,
)
from phlo.logging import get_logger
from phlo.operations.backup import verify_backup_set
from phlo.operations.journal import (
    OperationJournalError,
    OperationJournalStore,
    claim_operation,
    complete_operation,
    mark_submitted,
    read_or_replay,
)

logger = get_logger(__name__)


class RestoreError(RuntimeError):
    """A restore plan/apply contract violation with a stable code."""

    def __init__(self, code: str, identifiers: tuple[str, ...] = ()) -> None:
        self.code = code
        self.identifiers = identifiers
        super().__init__(f"{code}: {', '.join(identifiers)}")


def plan_restore(
    *,
    backup_set_dir: Path,
    target: RestoreTarget,
    now: datetime | None = None,
    plan_token: str | None = None,
) -> RestorePlan:
    """Create a mutation-free restore plan for a verified set and named target."""
    backup_set_dir = Path(backup_set_dir)
    verification = verify_backup_set(backup_set_dir)
    if not verification.accepted or verification.manifest is None:
        raise RestoreError("unverified_backup_set", (str(backup_set_dir),))

    manifest = _manifest_from_dir(backup_set_dir)
    _validate_target_planning(target, backup_set_dir)

    reference = now or datetime.now(UTC)
    token = plan_token or uuid4().hex
    return RestorePlan(
        schema_version=RESTORE_PLAN_SCHEMA_VERSION,
        plan_token=token,
        backup_set_dir=str(backup_set_dir.resolve()),
        backup_set_id=manifest.set_id,
        set_digest=manifest.manifest_digest(),
        target=target,
        provider_order=RESTORE_PROVIDER_ORDER,
        created_at=reference.isoformat(),
        expires_at=(reference + timedelta(seconds=RESTORE_PLAN_TTL_SECONDS)).isoformat(),
    )


def restore_apply(
    *,
    plan: RestorePlan,
    confirmation_token: str,
    contributors: Mapping[str, Any],
    journal: OperationJournalStore,
    subject: str = "operator",
    verify_fn: Any = None,
) -> RestoreResult:
    """Apply a plan only to the bound target after reverification and journal claim."""
    if plan.plan_token != confirmation_token:
        raise RestoreError("token_mismatch", (plan.plan_token,))
    if plan.is_expired():
        raise RestoreError("plan_expired", (plan.plan_token,))

    operation_id = _operation_id(plan)
    stored = read_or_replay(journal, operation_id)
    if stored is not None:
        # Exactly-once: a stored terminal result means this plan already ran.
        return _result_from_dict(stored)

    backup_set_dir = Path(plan.backup_set_dir)
    verification = (verify_fn or verify_backup_set)(backup_set_dir)
    if not verification.accepted or verification.manifest is None:
        raise RestoreError("unverified_backup_set", (plan.backup_set_id,))
    manifest = _manifest_from_dir(backup_set_dir)
    if manifest.manifest_digest() != plan.set_digest:
        raise RestoreError(
            "set_digest_mismatch",
            (
                plan.backup_set_id,
                plan.set_digest,
            ),
        )
    _validate_target_planning(plan.target, backup_set_dir)

    try:
        claim_operation(
            journal,
            operation_id=operation_id,
            subject=subject,
            action="restore.apply",
            target=plan.target.target_id,
            plan_token=plan.plan_token,
        )
        mark_submitted(journal, operation_id)
    except OperationJournalError as exc:
        raise RestoreError(exc.code, exc.identifiers) from exc

    steps: list[RestoreStepResult] = []
    try:
        for provider in plan.provider_order:
            contributor = contributors.get(provider)
            if contributor is None:
                raise RestoreError("missing_restore_provider", (provider,))
            artifacts = _artifacts_for(manifest, provider)
            steps.append(
                contributor.restore(plan.target, artifacts, plan.plan_token, plan.backup_set_dir)
            )
            if steps[-1].state is BackupContributorState.FAILED:
                return _failed_result(journal, operation_id, plan, steps, steps[-1].failure)
        reconciliation = _reconcile(contributors, plan, manifest)
    except Exception as exc:
        return _failed_result(
            journal,
            operation_id,
            plan,
            steps,
            {"reason": _safe_reason(exc)},
        )

    if not reconciliation.ok:
        return _failed_result(
            journal,
            operation_id,
            plan,
            steps,
            {"reason": "reconciliation_failed", "reasons": list(reconciliation.reasons)},
            reconciliation=reconciliation,
        )

    result = RestoreResult(
        state="succeeded",
        accepted=True,
        target_id=plan.target.target_id,
        plan_token=plan.plan_token,
        steps=tuple(steps),
        reconciliation=reconciliation,
    )
    complete_operation(journal, operation_id, result.to_dict())
    return result


def _reconcile(
    contributors: Mapping[str, RestoreContributor],
    plan: RestorePlan,
    manifest: BackupSetManifest,
) -> RestoreReconciliationResult:
    """Verify restored state across every authority that owns artifacts."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    for provider in BACKUP_PROVIDER_ORDER:
        contributor = contributors.get(provider)
        if contributor is None:
            checks[provider] = False
            reasons.append(f"{provider}:missing_contributor")
            continue
        artifacts = _artifacts_for(manifest, provider)
        if not artifacts:
            checks[provider] = True
            continue
        evidence = contributor.reconcile(
            plan.target, artifacts, plan.plan_token, plan.backup_set_dir
        )
        ok = bool(evidence.get("ok"))
        checks[provider] = ok
        if not ok:
            reasons.append(
                f"{provider}:" + (_safe_reason(evidence.get("reason")) or "reconcile_failed")
            )
    return RestoreReconciliationResult(
        ok=all(checks.values()),
        checks=checks,
        reasons=tuple(reasons),
    )


def _validate_target_planning(target: RestoreTarget, backup_set_dir: Path) -> None:
    if not target.target_id or not target.location:
        raise RestoreError("blank_target", ())
    location = Path(target.location).resolve()
    if location == backup_set_dir.resolve():
        raise RestoreError("source_as_target", (str(location),))
    if location in (Path.home(), Path(location.anchor)):
        raise RestoreError("unowned_target", (str(location),))
    if location.exists() and any(location.iterdir()):
        raise RestoreError("target_not_empty", (str(location),))


def _manifest_from_dir(backup_set_dir: Path) -> BackupSetManifest:
    try:
        payload = json.loads((backup_set_dir / "manifest.json").read_text(encoding="utf-8"))
        return BackupSetManifest.from_dict(payload)
    except (OSError, ValueError, BackupSetError) as exc:
        raise RestoreError("manifest_unreadable", (str(backup_set_dir), str(exc))) from exc


def _artifacts_for(manifest: BackupSetManifest, provider: str) -> Sequence[BackupArtifact]:
    return tuple(artifact for artifact in manifest.artifacts if artifact.provider == provider)


def restore_operation_id(plan: RestorePlan) -> str:
    """Canonical, durable journal id for one restore plan.

    Public so every surface (CLI, HTTP) resolves the same verification handle
    for a plan instead of re-deriving the digest format.
    """
    return _operation_id(plan)


def _operation_id(plan: RestorePlan) -> str:
    digest = sha256_bytes(canonical_json_bytes(plan.to_dict()))[:12]
    return f"restore.apply:{plan.target.target_id}:{digest}"


def _failed_result(
    journal: OperationJournalStore,
    operation_id: str,
    plan: RestorePlan,
    steps: Sequence[RestoreStepResult],
    failure: dict[str, Any] | None,
    *,
    reconciliation: RestoreReconciliationResult | None = None,
) -> RestoreResult:
    result = RestoreResult(
        state="failed",
        accepted=False,
        target_id=plan.target.target_id,
        plan_token=plan.plan_token,
        steps=tuple(steps),
        reconciliation=reconciliation,
        failure=failure,
    )
    complete_operation(journal, operation_id, result.to_dict())
    return result


def _safe_reason(exc: Any) -> str:
    from phlo.capabilities.continuity import redact_message

    if isinstance(exc, RestoreError):
        return exc.code
    if isinstance(exc, Exception):
        return redact_message(str(exc) or type(exc).__name__)
    return str(exc)


def _result_from_dict(stored: Mapping[str, Any]) -> RestoreResult:
    steps = tuple(RestoreStepResult.from_dict(step) for step in stored.get("steps") or ())
    reconciliation = stored.get("reconciliation")
    return RestoreResult(
        state=str(stored.get("state", "unknown")),
        accepted=bool(stored.get("accepted")),
        target_id=str(stored.get("target_id", "")),
        plan_token=str(stored.get("plan_token", "")),
        steps=steps,
        reconciliation=_reconciliation_from_dict(reconciliation) if reconciliation else None,
        failure=stored.get("failure"),
    )


def _reconciliation_from_dict(data: Mapping[str, Any]) -> RestoreReconciliationResult:
    return RestoreReconciliationResult(
        ok=bool(data.get("ok")),
        checks={str(k): bool(v) for k, v in dict(data.get("checks") or {}).items()},
        reasons=tuple(str(item) for item in data.get("reasons") or ()),
    )
