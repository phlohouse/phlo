"""Supported version upgrade (ADR 0049 §5, Plan 013).

Exactly one previous-to-candidate pair is executable; everything else refuses
before mutation. Upgrade planning requires a verified Plan 011 backup of the
exact source state and binds token to source/candidate/backup digest/migration
digest/target. Apply claims the Plan 010 journal, runs provider-owned steps in
order, and at the declared rollback-safe boundary either issues a restore
action (Plan 012) or emits the exact bounded forward-repair state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from phlo.capabilities.continuity import (
    BackupSetError,
    BackupSetManifest,
    RestoreTarget,
    canonical_json_bytes,
    redact_message,
    sha256_bytes,
)
from phlo.operations.journal import (
    OperationJournalError,
    OperationJournalStore,
    claim_operation,
    complete_operation,
    mark_submitted,
    read_or_replay,
)

UPGRADE_PLAN_SCHEMA_VERSION = "1"
UPGRADE_PLAN_TTL_SECONDS = 4 * 3600

# The accepted fixture policy pair (ADR 0049 §5): exactly one immutable
# declared transition. No real release gate is promoted; this proves journey.
SUPPORTED_FROM_VERSION = "0.14.0"
SUPPORTED_TO_VERSION = "0.15.0"

# The last rollback-safe step; steps after it are irreversible (no false
# rollback), only bounded forward repair.
ROLLBACK_SAFE_LAST_STEP = "postgres.schema"


class UpgradeStepState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class UpgradeStepPhase(StrEnum):
    PREFLIGHT = "preflight"
    SUBMISSION = "submission"


@dataclass(frozen=True, slots=True)
class UpgradeStepDef:
    """One ordered provider-owned migration step in the supported pair."""

    owner: str
    name: str
    phase: str
    rollback_safe: bool
    retry_safe_on_preflight: bool


UPGRADE_PIPELINE: tuple[UpgradeStepDef, ...] = (
    UpgradeStepDef("postgres", "postgres.schema", "migration", True, True),
    UpgradeStepDef("nessie", "nessie.catalog", "migration", False, False),
    UpgradeStepDef("iceberg", "iceberg.metadata", "migration", False, False),
    UpgradeStepDef("minio", "minio.policy", "policy", False, False),
)


def validate_upgrade_pair(from_version: str, to_version: str) -> None:
    """Refuse anything but the exact supported pair, before any mutation."""
    if from_version != SUPPORTED_FROM_VERSION or to_version != SUPPORTED_TO_VERSION:
        raise UpgradeError("unsupported_pair", (from_version, to_version))


def migration_digest() -> str:
    """Deterministic digest over the ordered pipeline identity."""
    payload = [{"name": step.name, "owner": step.owner} for step in UPGRADE_PIPELINE]
    return sha256_bytes(canonical_json_bytes(payload))


@dataclass(frozen=True, slots=True)
class UpgradeStepResult:
    """Outcome of one upgrade step with before/after version evidence."""

    owner: str
    name: str
    state: UpgradeStepState
    phase: UpgradeStepPhase
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    retry_safe: bool = False
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "state": self.state.value,
            "phase": self.phase.value,
            "before": self.before,
            "after": self.after,
            "retry_safe": self.retry_safe,
            "failure": self.failure,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UpgradeStepResult:
        return cls(
            owner=str(data["owner"]),
            name=str(data["name"]),
            state=UpgradeStepState(str(data["state"])),
            phase=UpgradeStepPhase(str(data["phase"])),
            before=dict(data.get("before") or {}),
            after=dict(data.get("after") or {}),
            retry_safe=bool(data.get("retry_safe", False)),
            failure=data.get("failure"),
        )

    @staticmethod
    def ok(
        defn: UpgradeStepDef, before: dict[str, Any], after: dict[str, Any]
    ) -> UpgradeStepResult:
        return UpgradeStepResult(
            owner=defn.owner,
            name=defn.name,
            state=UpgradeStepState.SUCCEEDED,
            phase=UpgradeStepPhase.SUBMISSION,
            before=before,
            after=after,
            retry_safe=True,
        )

    @staticmethod
    def not_applicable(defn: UpgradeStepDef) -> UpgradeStepResult:
        return UpgradeStepResult(
            owner=defn.owner,
            name=defn.name,
            state=UpgradeStepState.NOT_APPLICABLE,
            phase=UpgradeStepPhase.PREFLIGHT,
            retry_safe=True,
        )

    @staticmethod
    def fail(defn: UpgradeStepDef, phase: UpgradeStepPhase, reason: str) -> UpgradeStepResult:
        return UpgradeStepResult(
            owner=defn.owner,
            name=defn.name,
            state=UpgradeStepState.FAILED,
            phase=phase,
            retry_safe=defn.retry_safe_on_preflight and phase is UpgradeStepPhase.PREFLIGHT,
            failure={"reason": redact_message(reason)},
        )


@dataclass(frozen=True, slots=True)
class UpgradePlan:
    """A mutation-free upgrade plan bound to backup digest + target."""

    schema_version: str
    plan_token: str
    from_version: str
    to_version: str
    backup_set_dir: str
    backup_set_id: str
    backup_digest: str
    migration_digest: str
    target: RestoreTarget
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_token": self.plan_token,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "backup_set_dir": self.backup_set_dir,
            "backup_set_id": self.backup_set_id,
            "backup_digest": self.backup_digest,
            "migration_digest": self.migration_digest,
            "target": self.target.to_dict(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UpgradePlan:
        return cls(
            schema_version=str(data["schema_version"]),
            plan_token=str(data["plan_token"]),
            from_version=str(data["from_version"]),
            to_version=str(data["to_version"]),
            backup_set_dir=str(data["backup_set_dir"]),
            backup_set_id=str(data["backup_set_id"]),
            backup_digest=str(data["backup_digest"]),
            migration_digest=str(data["migration_digest"]),
            target=RestoreTarget.from_dict(dict(data["target"])),
            created_at=str(data["created_at"]),
            expires_at=str(data["expires_at"]),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        reference = now or datetime.now(UTC)
        if expiry.tzinfo is None or reference.tzinfo is None:
            return False
        return reference >= expiry


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    state: str
    accepted: bool
    plan_token: str
    from_version: str
    to_version: str
    steps: tuple[UpgradeStepResult, ...] = ()
    rollback_action: str | None = None
    forward_repair: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "accepted": self.accepted,
            "plan_token": self.plan_token,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "steps": [step.to_dict() for step in self.steps],
            "rollback_action": self.rollback_action,
            "forward_repair": self.forward_repair,
            "reconciliation": self.reconciliation,
            "failure": self.failure,
        }


class UpgradeContributor(Protocol):
    """A provider-owned upgrade step + post-upgrade reconciliation."""

    def upgrade_step(
        self,
        defn: UpgradeStepDef,
        target: RestoreTarget,
        from_version: str,
        to_version: str,
        plan_token: str,
    ) -> UpgradeStepResult:
        """Run the named step; return before/after evidence and phase."""
        ...

    def upgrade_reconcile(
        self, target: RestoreTarget, to_version: str, plan_token: str
    ) -> dict[str, Any]:
        """Return post-upgrade evidence; must include ``ok`` and ``reasons``."""
        ...


class UpgradeError(RuntimeError):
    def __init__(self, code: str, identifiers: tuple[str, ...] = ()) -> None:
        self.code = code
        self.identifiers = identifiers
        super().__init__(f"{code}: {', '.join(identifiers)}")


def plan_upgrade(
    *,
    from_version: str,
    to_version: str,
    backup_set_dir: str | Path,
    target: RestoreTarget,
    now: datetime | None = None,
) -> UpgradePlan:
    """Create a mutation-free upgrade plan after a verified backup."""
    validate_upgrade_pair(from_version, to_version)
    manifest = _verified_manifest(backup_set_dir)
    reference = now or datetime.now(UTC)
    return UpgradePlan(
        schema_version=UPGRADE_PLAN_SCHEMA_VERSION,
        plan_token=uuid4().hex,
        from_version=from_version,
        to_version=to_version,
        backup_set_dir=str(Path(backup_set_dir).resolve()),
        backup_set_id=manifest.set_id,
        backup_digest=manifest.manifest_digest(),
        migration_digest=migration_digest(),
        target=target,
        created_at=reference.isoformat(),
        expires_at=(reference + timedelta(seconds=UPGRADE_PLAN_TTL_SECONDS)).isoformat(),
    )


def upgrade_apply(
    *,
    plan: UpgradePlan,
    confirmation_token: str,
    contributors: Mapping[str, Any],
    journal: OperationJournalStore,
    subject: str = "operator",
) -> UpgradeResult:
    """Apply the bound upgrade, running provider steps in ordered pipeline."""
    validate_upgrade_pair(plan.from_version, plan.to_version)
    if plan.plan_token != confirmation_token:
        raise UpgradeError("token_mismatch", (plan.plan_token,))
    if plan.is_expired():
        raise UpgradeError("plan_expired", (plan.plan_token,))

    operation_id = _operation_id(plan)
    stored = read_or_replay(journal, operation_id)
    if stored is not None:
        return _result_from_dict(stored)

    manifest = _verified_manifest(plan.backup_set_dir)
    if manifest.manifest_digest() != plan.backup_digest:
        raise UpgradeError("backup_digest_mismatch", (plan.backup_set_id,))
    if plan.migration_digest != migration_digest():
        raise UpgradeError("migration_digest_mismatch", ())

    try:
        claim_operation(
            journal,
            operation_id=operation_id,
            subject=subject,
            action="upgrade.apply",
            target=plan.target.target_id,
            plan_token=plan.plan_token,
        )
        mark_submitted(journal, operation_id)
    except OperationJournalError as exc:
        raise UpgradeError(exc.code, exc.identifiers) from exc

    steps: list[UpgradeStepResult] = []
    try:
        for index, defn in enumerate(UPGRADE_PIPELINE):
            contributor = contributors.get(defn.owner)
            result: UpgradeStepResult
            if contributor is None:
                result = UpgradeStepResult.not_applicable(defn)
            else:
                result = contributor.upgrade_step(
                    defn, plan.target, plan.from_version, plan.to_version, plan.plan_token
                )
            steps.append(result)
            if result.state is UpgradeStepState.FAILED:
                rollback_safe = index <= _max_rollback_safe_index()
                if rollback_safe and defn.rollback_safe:
                    return _finish_failed(
                        journal,
                        operation_id,
                        plan,
                        steps,
                        rollback_action="restore",
                        failure={
                            "reason": "upgrade failed before rollback boundary",
                            "failed_step": defn.name,
                        },
                    )
                return _finish_failed(
                    journal,
                    operation_id,
                    plan,
                    steps,
                    forward_repair=_forward_repair(defn.name, plan),
                    failure={
                        "reason": "upgrade failed after rollback boundary",
                        "failed_step": defn.name,
                    },
                )
    except Exception as exc:
        return _finish_failed(
            journal,
            operation_id,
            plan,
            steps,
            failure={"reason": redact_message(str(exc))},
        )

    checks: dict[str, bool] = {}
    reasons: list[str] = []
    for defn in UPGRADE_PIPELINE:
        contributor = contributors.get(defn.owner)
        if contributor is None:
            continue
        evidence = contributor.upgrade_reconcile(plan.target, plan.to_version, plan.plan_token)
        ok = bool(evidence.get("ok"))
        checks[defn.name] = ok
        if not ok:
            reasons.append(defn.name + ":" + (str(evidence.get("reason")) or "reconcile_failed"))

    final_result = UpgradeResult(
        state="succeeded",
        accepted=not reasons,
        plan_token=plan.plan_token,
        from_version=plan.from_version,
        to_version=plan.to_version,
        steps=tuple(steps),
        reconciliation={"ok": not reasons, "checks": checks, "reasons": reasons},
    )
    complete_operation(journal, operation_id, final_result.to_dict())
    return final_result


def _verified_manifest(backup_set_dir: str | Path) -> BackupSetManifest:
    from phlo.operations.backup import verify_backup_set

    backup_set_dir = Path(backup_set_dir)
    verification = verify_backup_set(backup_set_dir)
    if not verification.accepted or verification.manifest is None:
        raise UpgradeError("unverified_backup_set", (str(backup_set_dir),))
    try:
        import json

        payload = json.loads((backup_set_dir / "manifest.json").read_text(encoding="utf-8"))
        return BackupSetManifest.from_dict(payload)
    except (OSError, ValueError, BackupSetError) as exc:
        raise UpgradeError("manifest_unreadable", (str(backup_set_dir), str(exc))) from exc


def _max_rollback_safe_index() -> int:
    for index, step in enumerate(UPGRADE_PIPELINE):
        if step.name == ROLLBACK_SAFE_LAST_STEP:
            return index
    return -1


def _forward_repair(failed_step: str, plan: UpgradePlan) -> dict[str, Any]:
    remaining = [step.name for step in UPGRADE_PIPELINE if step.name != failed_step]
    return {
        "instruction": "complete the declared migrations then reconcile",
        "remaining_steps": remaining,
        "backup_set_id": plan.backup_set_id,
        "must_not_rollback": True,
    }


def _finish_failed(
    journal: OperationJournalStore,
    operation_id: str,
    plan: UpgradePlan,
    steps: Sequence[UpgradeStepResult],
    *,
    rollback_action: str | None = None,
    forward_repair: dict[str, Any] | None = None,
    failure: dict[str, Any] | None,
) -> UpgradeResult:
    result = UpgradeResult(
        state="failed",
        accepted=False,
        plan_token=plan.plan_token,
        from_version=plan.from_version,
        to_version=plan.to_version,
        steps=tuple(steps),
        rollback_action=rollback_action,
        forward_repair=forward_repair,
        failure=failure,
    )
    complete_operation(journal, operation_id, result.to_dict())
    return result


def _operation_id(plan: UpgradePlan) -> str:
    digest = sha256_bytes(canonical_json_bytes(plan.to_dict()))[:12]
    return f"upgrade.apply:{plan.target.target_id}:{digest}"


def _result_from_dict(stored: Mapping[str, Any]) -> UpgradeResult:
    return UpgradeResult(
        state=str(stored.get("state", "unknown")),
        accepted=bool(stored.get("accepted")),
        plan_token=str(stored.get("plan_token", "")),
        from_version=str(stored.get("from_version", "")),
        to_version=str(stored.get("to_version", "")),
        steps=tuple(UpgradeStepResult.from_dict(step) for step in stored.get("steps") or ()),
        rollback_action=stored.get("rollback_action"),
        forward_repair=stored.get("forward_repair"),
        reconciliation=stored.get("reconciliation"),
        failure=stored.get("failure"),
    )
