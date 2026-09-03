"""Neutral backup-set and continuity contracts (ADR 0049 §3, Plan 011 Step 1).

Core owns the versioned backup-set manifest, artifact identity, canonical
serialization/digests, sanitized failures, and the stable verification
reasons. Providers own mechanics through the :class:`BackupContributor`
protocol and never finalize a set; core coordinates ordering, hashing, and
atomic finalization without importing any provider package.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

BACKUP_SET_SCHEMA_VERSION = "1"  # the only manifest schema this core reads/writes

SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({"1"})  # verify fails closed otherwise

BACKUP_PROVIDER_ORDER: tuple[str, ...] = ("postgres", "nessie", "minio", "iceberg")
# Fixed contributor order (ADR 0049 §3): metadata before data blobs.
# Iceberg table metadata and snapshots are covered by the MinIO object backup
# plus the Iceberg metadata inventory contribution.

SET_MANIFEST_NAME = "manifest.json"
SET_MANIFEST_STAGING_NAME = "manifest.json.partial"  # a set without the final name is unusable

TREE_DIGEST_DOMAIN = b"phlo-recovery-tree-v1\0"  # keeps tree digests distinct from file digests

_REDACTED_KEY_MARKERS = ("password", "secret", "token", "credential", "authorization", "api_key")


class BackupContributorState(StrEnum):
    """Stable contributor lifecycle states shared by all providers."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BackupVerificationReason(StrEnum):
    """Stable, machine-readable reasons returned by read-only verification."""

    MISSING_MANIFEST = "missing_manifest"
    CORRUPT_MANIFEST = "corrupt_manifest"
    UNKNOWN_SCHEMA_VERSION = "unknown_schema_version"
    MISSING_PROVIDER = "missing_provider"
    UNKNOWN_PROVIDER = "unknown_provider"
    INCOMPATIBLE_VERSION = "incompatible_version"
    PARTIAL_SET = "partial_set"
    WRONG_OWNER = "wrong_owner"
    MIXED_RUN = "mixed_run"
    PATH_ESCAPE = "path_escape"
    SYMLINK = "symlink"
    MISSING_ARTIFACT = "missing_artifact"
    SIZE_MISMATCH = "size_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    EXTRA_ARTIFACT = "extra_artifact"


class BackupSetError(ValueError):
    """A manifest or artifact contract violation with a stable reason."""

    def __init__(self, reason: BackupVerificationReason | str, message: str) -> None:
        self.reason = BackupVerificationReason(reason) if isinstance(reason, str) else reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    """One immutable provider artifact with identity and content digests."""

    provider: str
    name: str
    relative_path: str
    size_bytes: int
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "name": self.name,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BackupArtifact:
        try:
            return cls(
                provider=_required_str(data, "provider"),
                name=_required_str(data, "name"),
                relative_path=_required_str(data, "relative_path"),
                size_bytes=int(data["size_bytes"]),
                sha256=_required_str(data, "sha256"),
                metadata=dict(data.get("metadata") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST, f"invalid artifact record: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class BackupContributorResult:
    """One provider's contribution outcome with sanitized failure evidence."""

    provider: str
    state: BackupContributorState
    artifacts: tuple[BackupArtifact, ...] = ()
    operation_id: str = ""
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "operation_id": self.operation_id,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "failure": self.failure,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BackupContributorResult:
        try:
            return cls(
                provider=_required_str(data, "provider"),
                state=BackupContributorState(_required_str(data, "state")),
                operation_id=str(data.get("operation_id") or ""),
                artifacts=tuple(
                    BackupArtifact.from_dict(artifact) for artifact in data.get("artifacts") or ()
                ),
                failure=data.get("failure"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST, f"invalid contributor record: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class BackupSetManifest:
    """The versioned, finalized backup-set contract (ADR 0049 §3)."""

    schema_version: str
    set_id: str
    source_deployment_id: str
    created_at: str
    versions: dict[str, str]
    quiesce: dict[str, Any]
    contributors: tuple[BackupContributorResult, ...]
    artifacts: tuple[BackupArtifact, ...]
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "set_id": self.set_id,
            "source_deployment_id": self.source_deployment_id,
            "created_at": self.created_at,
            "versions": dict(sorted(self.versions.items())),
            "quiesce": self.quiesce,
            "complete": self.complete,
            "contributors": [contributor.to_dict() for contributor in self.contributors],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BackupSetManifest:
        if not isinstance(data, Mapping):
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST, "manifest is not a JSON object"
            )
        schema_version = data.get("schema_version")
        if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            raise BackupSetError(
                BackupVerificationReason.UNKNOWN_SCHEMA_VERSION,
                f"unsupported manifest schema_version: {schema_version!r}",
            )
        try:
            manifest = cls(
                schema_version=str(schema_version),
                set_id=_required_str(data, "set_id"),
                source_deployment_id=_required_str(data, "source_deployment_id"),
                created_at=_required_str(data, "created_at"),
                versions={str(k): str(v) for k, v in dict(data.get("versions") or {}).items()},
                quiesce=dict(data.get("quiesce") or {}),
                contributors=tuple(
                    BackupContributorResult.from_dict(record)
                    for record in data.get("contributors") or ()
                ),
                artifacts=tuple(
                    BackupArtifact.from_dict(artifact) for artifact in data.get("artifacts") or ()
                ),
                complete=bool(data.get("complete")),
            )
        except BackupSetError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST, f"invalid manifest: {exc}"
            ) from exc
        if not manifest.set_id or not manifest.source_deployment_id:
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST,
                "manifest must record set_id and source_deployment_id",
            )
        return manifest

    def canonical_bytes(self) -> bytes:
        """Return the canonical JSON serialization used for digests."""
        return canonical_json_bytes(self.to_dict())

    def manifest_digest(self) -> str:
        """Return the SHA-256 digest of the canonical manifest serialization."""
        return sha256_bytes(self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class BackupCreateResult:
    """Provider-neutral evidence for one backup create attempt."""

    set_id: str
    target: str
    state: str
    accepted: bool
    manifest: dict[str, Any] | None = None
    contributors: tuple[dict[str, Any], ...] = ()
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "target": self.target,
            "state": self.state,
            "accepted": self.accepted,
            "manifest": self.manifest,
            "contributors": list(self.contributors),
            "failure": self.failure,
        }


@dataclass(frozen=True, slots=True)
class BackupVerifyResult:
    """Read-only verification outcome with stable reasons."""

    set_id: str
    state: str
    accepted: bool
    reasons: tuple[str, ...] = ()
    manifest: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "state": self.state,
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "manifest": self.manifest,
        }


class BackupContributor(Protocol):
    """A provider-owned backup contributor (ADR 0049 §3).

    The contributor receives an explicit destination (its owned staging
    prefix) and an operation ID, writes only beneath that prefix, returns
    artifact descriptors, and never finalizes the set.
    """

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        """Capture this provider's state beneath ``destination``."""
        ...


RESTORE_PLAN_SCHEMA_VERSION = "1"
RESTORE_PLAN_TTL_SECONDS = 24 * 3600
# Restore is the reverse of the backup order (ADR 0049 §4): Iceberg metadata
# is restored with the object data, then MinIO/Nessie (data before catalog),
# then PostgreSQL last.
RESTORE_PROVIDER_ORDER: tuple[str, ...] = ("iceberg", "minio", "nessie", "postgres")


class RestoreStepPhase(StrEnum):
    """Provider restore phases used to classify where a failure occurred."""

    PREFLIGHT = "preflight"
    SUBMISSION = "submission"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class RestoreTarget:
    """An explicitly named and located restore destination (never implicit)."""

    target_id: str
    location: str

    @classmethod
    def of(cls, location: str | os.PathLike[str]) -> RestoreTarget:
        from pathlib import Path as _Path

        resolved = _Path(location).resolve()
        return cls(target_id=str(resolved), location=str(resolved))

    def to_dict(self) -> dict[str, Any]:
        return {"target_id": self.target_id, "location": self.location}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RestoreTarget:
        return cls(
            target_id=_required_str(data, "target_id"), location=_required_str(data, "location")
        )


@dataclass(frozen=True, slots=True)
class RestoreStepResult:
    """One provider's restore outcome with phase and retry classification."""

    provider: str
    state: BackupContributorState
    phase: RestoreStepPhase
    retry_safe: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "phase": self.phase.value,
            "retry_safe": self.retry_safe,
            "evidence": self.evidence,
            "failure": self.failure,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RestoreStepResult:
        try:
            return cls(
                provider=_required_str(data, "provider"),
                state=BackupContributorState(_required_str(data, "state")),
                phase=RestoreStepPhase(_required_str(data, "phase")),
                retry_safe=bool(data.get("retry_safe", False)),
                evidence=dict(data.get("evidence") or {}),
                failure=data.get("failure"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST, f"invalid restore step: {exc}"
            ) from exc

    @staticmethod
    def fail_step(
        provider: str,
        phase: RestoreStepPhase,
        reason: str,
        *,
        retry_safe: bool = False,
        evidence: dict[str, Any] | None = None,
    ) -> RestoreStepResult:
        return RestoreStepResult(
            provider=provider,
            state=BackupContributorState.FAILED,
            phase=phase,
            retry_safe=retry_safe,
            evidence=evidence or {},
            failure={"reason": redact_message(reason)},
        )

    @staticmethod
    def ok(
        provider: str,
        phase: RestoreStepPhase = RestoreStepPhase.SUBMISSION,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> RestoreStepResult:
        return RestoreStepResult(
            provider=provider,
            state=BackupContributorState.SUCCEEDED,
            phase=phase,
            retry_safe=True,
            evidence=evidence or {},
            failure=None,
        )


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """A mutation-free restore plan bound to set digest, target, and expiry."""

    schema_version: str
    plan_token: str
    backup_set_dir: str
    backup_set_id: str
    set_digest: str
    target: RestoreTarget
    provider_order: tuple[str, ...]
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_token": self.plan_token,
            "backup_set_dir": self.backup_set_dir,
            "backup_set_id": self.backup_set_id,
            "set_digest": self.set_digest,
            "target": self.target.to_dict(),
            "provider_order": list(self.provider_order),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RestorePlan:
        try:
            return cls(
                schema_version=_required_str(data, "schema_version"),
                plan_token=_required_str(data, "plan_token"),
                backup_set_dir=_required_str(data, "backup_set_dir"),
                backup_set_id=_required_str(data, "backup_set_id"),
                set_digest=_required_str(data, "set_digest"),
                target=RestoreTarget.from_dict(dict(data["target"])),
                provider_order=tuple(
                    str(item) for item in (data.get("provider_order") or RESTORE_PROVIDER_ORDER)
                ),
                created_at=_required_str(data, "created_at"),
                expires_at=_required_str(data, "expires_at"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupSetError(
                BackupVerificationReason.CORRUPT_MANIFEST, f"invalid plan: {exc}"
            ) from exc

    def is_expired(self, now: datetime | None = None) -> bool:
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        reference = now or datetime.now(UTC)
        return expiry.tzinfo is None or reference.tzinfo is None or reference >= expiry


@dataclass(frozen=True, slots=True)
class RestoreReconciliationResult:
    """Post-restore verification across every evidence authority."""

    ok: bool
    checks: dict[str, bool]
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": self.checks, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Provider-neutral evidence for one restore apply attempt."""

    state: str
    accepted: bool
    target_id: str
    plan_token: str
    steps: tuple[RestoreStepResult, ...] = ()
    reconciliation: RestoreReconciliationResult | None = None
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "accepted": self.accepted,
            "target_id": self.target_id,
            "plan_token": self.plan_token,
            "steps": [step.to_dict() for step in self.steps],
            "reconciliation": self.reconciliation.to_dict() if self.reconciliation else None,
            "failure": self.failure,
        }


class RestoreContributor(Protocol):
    """A provider-owned restorer applying only its own artifacts to a target.

    ``restore`` records before/after evidence and the phase; a failure before
    submission is never retry-safe. ``reconcile`` returns explicit post-restore
    checks across the provider's authority. Providers read their own verified
    artifacts from ``backup_set_dir`` and write under ``target``.
    """

    def restore(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> RestoreStepResult:
        """Apply this provider's artifacts to ``target``."""
        ...

    def reconcile(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> dict[str, Any]:
        """Return post-restore evidence; must include ``ok`` and ``reasons``."""
        ...


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize to canonical JSON: sorted keys, tight separators, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_bytes(payload: bytes) -> str:
    """Return the hex SHA-256 digest of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a file in 1 MiB blocks and return the hex digest."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    """Digest every file under ``root`` in sorted path order.

    Rejects symlinks and special files so the digest always describes plain
    regular-file content. The domain separator keeps tree digests distinct
    from single-file digests.
    """
    root = Path(root)
    digest = hashlib.sha256(TREE_DIGEST_DOMAIN)
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise BackupSetError(
                BackupVerificationReason.SYMLINK, f"tree contains unsupported entry: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise BackupSetError(
                BackupVerificationReason.PATH_ESCAPE, f"tree contains unsupported entry: {path}"
            )
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def redact_failure(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a deep copy of ``payload`` with secret-looking keys redacted."""
    if payload is None:
        return None
    return _redact_value(dict(payload))


def redact_message(message: str) -> str:
    """Strip secret-looking ``key=value`` assignments from a message."""
    words = message.split()
    return " ".join(
        "REDACTED" if any(marker in word.lower() for marker in _REDACTED_KEY_MARKERS) else word
        for word in words
    )


def validate_contained_path(set_dir: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``set_dir`` or reject path escape."""
    if not relative_path or Path(relative_path).is_absolute():
        raise BackupSetError(
            BackupVerificationReason.PATH_ESCAPE,
            f"artifact path is not relative: {relative_path!r}",
        )
    resolved = (set_dir / relative_path).resolve()
    try:
        resolved.relative_to(set_dir.resolve())
    except ValueError as exc:
        raise BackupSetError(
            BackupVerificationReason.PATH_ESCAPE,
            f"artifact path escapes the backup set: {relative_path!r}",
        ) from exc
    return resolved


def write_manifest_atomically(set_dir: Path, manifest: BackupSetManifest) -> None:
    """Write the manifest to a staging name, then atomically finalize it.

    The set only becomes usable (a ``manifest.json`` exists) after this
    atomic rename, so a crash mid-write leaves a visibly unusable set.
    """
    staging = set_dir / SET_MANIFEST_STAGING_NAME
    staging.write_bytes(manifest.canonical_bytes())
    staging.replace(set_dir / SET_MANIFEST_NAME)


def fail_contributor(provider: str, reason: str, operation_id: str = "") -> BackupContributorResult:
    """Build a sanitized failed contributor result."""
    return BackupContributorResult(
        provider=provider,
        state=BackupContributorState.FAILED,
        operation_id=operation_id,
        failure={"reason": redact_message(reason)},
    )


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in _REDACTED_KEY_MARKERS):
                redacted[str(key)] = "REDACTED"
            else:
                redacted[str(key)] = _redact_value(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _required_str(data: Mapping[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


__all__ = [
    "BACKUP_PROVIDER_ORDER",
    "BACKUP_SET_SCHEMA_VERSION",
    "RESTORE_PLAN_SCHEMA_VERSION",
    "RESTORE_PLAN_TTL_SECONDS",
    "RESTORE_PROVIDER_ORDER",
    "SET_MANIFEST_NAME",
    "SET_MANIFEST_STAGING_NAME",
    "SUPPORTED_MANIFEST_SCHEMA_VERSIONS",
    "BackupArtifact",
    "BackupContributor",
    "BackupContributorResult",
    "BackupContributorState",
    "BackupCreateResult",
    "BackupSetError",
    "BackupSetManifest",
    "BackupVerificationReason",
    "BackupVerifyResult",
    "RestoreContributor",
    "RestorePlan",
    "RestoreReconciliationResult",
    "RestoreResult",
    "RestoreStepPhase",
    "RestoreStepResult",
    "RestoreTarget",
    "canonical_json_bytes",
    "fail_contributor",
    "redact_failure",
    "redact_message",
    "sha256_bytes",
    "sha256_file",
    "sha256_tree",
    "validate_contained_path",
    "write_manifest_atomically",
]
