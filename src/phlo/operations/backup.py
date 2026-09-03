"""Backup create/verify coordination (ADR 0049 §3, Plan 011 Steps 3-4).

Core owns ordering, hashing, atomic finalization, and journal integration;
providers own mechanics through the neutral ``BackupContributor`` contract.
``create_backup_set`` is authorized, journaled, fail-before-mutation, and
leaves a partial set visibly unusable (no finalized manifest).
``verify_backup_set`` is read-only and mutation-free.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from phlo.capabilities.continuity import (
    BACKUP_PROVIDER_ORDER,
    BACKUP_SET_SCHEMA_VERSION,
    SET_MANIFEST_NAME,
    BackupArtifact,
    BackupContributor,
    BackupContributorResult,
    BackupContributorState,
    BackupCreateResult,
    BackupSetError,
    BackupSetManifest,
    BackupVerificationReason,
    BackupVerifyResult,
    fail_contributor,
    redact_message,
    sha256_file,
    validate_contained_path,
    write_manifest_atomically,
)
from phlo.logging import get_logger
from phlo.operations.journal import (
    OperationJournalStore,
    claim_operation,
    complete_operation,
    mark_submitted,
    read_or_replay,
)
from phlo.operations.runtime import get_deployment_id, get_package_versions

logger = get_logger(__name__)

QuiesceHook = Callable[[], Mapping[str, Any]]


def _default_quiesce() -> Mapping[str, Any]:
    return {"quiesced": True, "strategy": "coordinator-default"}


def default_backup_contributors() -> list[tuple[str, BackupContributor]]:
    """Resolve the registered backup contributors in ADR 0049 order.

    Raises ``LookupError`` when any blessed provider is missing so a partial
    contributor roster can never silently produce an incomplete set.
    """
    from phlo.capabilities import list_capabilities, resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    registered = set(list_capabilities("backup_contributor"))
    resolved: list[tuple[str, BackupContributor]] = []
    for provider in BACKUP_PROVIDER_ORDER:
        if provider not in registered:
            raise LookupError(f"no backup contributor registered for {provider!r}")
        resolution = resolve_capability("backup_contributor", provider)
        if resolution is None:
            raise LookupError(f"backup contributor {provider!r} did not resolve")
        resolved.append((provider, resolution.provider))
    return resolved


def create_backup_set(
    *,
    target: Path,
    contributors: Sequence[tuple[str, BackupContributor]],
    journal: OperationJournalStore,
    subject: str = "operator",
    set_id: str | None = None,
    deployment_id: str | None = None,
    versions: Mapping[str, str] | None = None,
    quiesce: QuiesceHook | None = None,
) -> BackupCreateResult:
    """Create one immutable, verified backup set under ``target``.

    Validates a new/owned target, claims the journal, quiesces writes, marks
    ``submitted`` before the first provider mutation, invokes contributors in
    the fixed order beneath their owned prefixes, hashes every artifact, and
    atomically finalizes the manifest only after completeness. On any failure
    the journal records ``failed`` and the staging set stays unusable.
    """
    target = Path(target)
    resolved_set_id = set_id or uuid4().hex
    operation_id = f"backup.create:{resolved_set_id}"

    seen_providers: set[str] = set()
    for provider, _contributor in contributors:
        if provider in seen_providers:
            raise BackupSetError(
                BackupVerificationReason.MIXED_RUN,
                f"duplicate backup contributor for {provider!r}",
            )
        seen_providers.add(provider)

    # The creator and the verifier must enforce the same invariant: the full
    # frozen ADR 0049 roster in canonical order. An incomplete, unknown,
    # duplicated, or wrongly-ordered roster is rejected before we claim the
    # journal or touch the target, so a partial set can never be finalized.
    provided_order = [provider for provider, _contributor in contributors]
    if provided_order != list(BACKUP_PROVIDER_ORDER):
        missing = [p for p in BACKUP_PROVIDER_ORDER if p not in provided_order]
        unknown = [p for p in provided_order if p not in BACKUP_PROVIDER_ORDER]
        if unknown:
            raise BackupSetError(
                BackupVerificationReason.UNKNOWN_PROVIDER,
                f"unknown backup providers in roster: {unknown}",
            )
        if missing:
            raise BackupSetError(
                BackupVerificationReason.MISSING_PROVIDER,
                f"backup contributor roster must cover the frozen ADR 0049 order; "
                f"missing providers: {missing}",
            )
        raise BackupSetError(
            BackupVerificationReason.MIXED_RUN,
            f"backup contributor roster is out of canonical ADR 0049 order: {provided_order}",
        )

    stored = read_or_replay(journal, operation_id)
    if stored is not None:
        return _result_from_dict(stored)

    _validate_target(target)

    quiesce_hook = quiesce or _default_quiesce
    set_dir = target / resolved_set_id

    claim_operation(
        journal,
        operation_id=operation_id,
        subject=subject,
        action="backup.create",
        target=str(target),
        plan_token=resolved_set_id,
    )

    try:
        quiesce_evidence = dict(quiesce_hook())
        # Fail-before-mutation: nothing is written until the claim is held
        # and the journal records submitted.
        mark_submitted(journal, operation_id)
    except Exception as exc:
        return _failed_result(
            journal=journal,
            operation_id=operation_id,
            set_id=resolved_set_id,
            target=target,
            contributor_results=(),
            failure=_sanitize_failure(exc),
        )

    contributor_results: list[BackupContributorResult] = []
    artifacts: list[BackupArtifact] = []

    try:
        set_dir.mkdir(parents=True, exist_ok=False)
        for provider, contributor in contributors:
            destination = set_dir / provider
            try:
                result = contributor.contribute(destination=destination, operation_id=operation_id)
                _validate_contributor_result(provider, result, operation_id)
            except Exception as exc:
                contributor_results.append(fail_contributor(provider, str(exc), operation_id))
                raise
            contributor_results.append(result)
            for artifact in result.artifacts:
                artifacts.append(_hash_artifact(set_dir, artifact))
    except Exception as exc:
        failure = _sanitize_failure(exc)
        contributor_results = _ensure_failed_record(contributor_results, failure, operation_id)
        return _failed_result(
            journal=journal,
            operation_id=operation_id,
            set_id=resolved_set_id,
            target=target,
            contributor_results=contributor_results,
            failure=failure,
        )

    manifest = BackupSetManifest(
        schema_version=BACKUP_SET_SCHEMA_VERSION,
        set_id=resolved_set_id,
        source_deployment_id=deployment_id or get_deployment_id(),
        created_at=datetime.now(UTC).isoformat(),
        versions=dict(versions) if versions else get_package_versions(),
        quiesce=quiesce_evidence,
        contributors=tuple(contributor_results),
        artifacts=tuple(sorted(artifacts, key=lambda a: (a.provider, a.relative_path))),
        complete=True,
    )
    try:
        write_manifest_atomically(set_dir, manifest)
    except Exception as exc:
        failure = _sanitize_failure(exc)
        logger.warning("backup_finalize_failed", set_id=resolved_set_id)
        return _failed_result(
            journal=journal,
            operation_id=operation_id,
            set_id=resolved_set_id,
            target=target,
            contributor_results=contributor_results,
            failure=failure,
        )

    result = BackupCreateResult(
        set_id=resolved_set_id,
        target=str(target),
        state="succeeded",
        accepted=True,
        manifest=manifest.to_dict(),
        contributors=tuple(record.to_dict() for record in contributor_results),
    )
    complete_operation(journal, operation_id, result.to_dict())
    return result


def verify_backup_set(
    set_dir: Path,
    *,
    expected_deployment_id: str | None = None,
) -> BackupVerifyResult:
    """Independently verify a finalized backup set without mutating anything.

    Parses only the finalized manifest, rejects unknown schema/providers/
    versions, checks exact provider membership and source ownership, and
    recalculates sizes and digests. Partial, mixed-run, corrupt, or foreign
    sets never verify.
    """
    set_dir = Path(set_dir)
    manifest_path = set_dir / SET_MANIFEST_NAME
    if not manifest_path.is_file():
        return _verify_failed("", (BackupVerificationReason.MISSING_MANIFEST.value,), None)

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = BackupSetManifest.from_dict(payload)
    except (OSError, ValueError) as exc:
        reason = (
            BackupVerificationReason.UNKNOWN_SCHEMA_VERSION
            if isinstance(exc, BackupSetError)
            and exc.reason is BackupVerificationReason.UNKNOWN_SCHEMA_VERSION
            else BackupVerificationReason.CORRUPT_MANIFEST
        )
        set_id = _extract_set_id(payload) if isinstance(exc, BackupSetError) else ""
        return _verify_failed(set_id, (reason.value,), None)

    reasons: list[str] = []
    declared_providers = [record.provider for record in manifest.contributors]
    for provider in BACKUP_PROVIDER_ORDER:
        if provider not in declared_providers:
            reasons.append(BackupVerificationReason.MISSING_PROVIDER.value)
    for provider in declared_providers:
        if provider not in BACKUP_PROVIDER_ORDER:
            reasons.append(BackupVerificationReason.UNKNOWN_PROVIDER.value)
        if declared_providers.count(provider) > 1:
            reasons.append(BackupVerificationReason.MIXED_RUN.value)

    if not manifest.complete or any(
        record.state is not BackupContributorState.SUCCEEDED for record in manifest.contributors
    ):
        reasons.append(BackupVerificationReason.PARTIAL_SET.value)

    if not manifest.source_deployment_id or (
        expected_deployment_id is not None
        and manifest.source_deployment_id != expected_deployment_id
    ):
        reasons.append(BackupVerificationReason.WRONG_OWNER.value)

    if not manifest.versions.get("phlo"):
        reasons.append(BackupVerificationReason.INCOMPATIBLE_VERSION.value)

    operation_ids = {record.operation_id for record in manifest.contributors if record.operation_id}
    if len(operation_ids) > 1 or any(
        record.operation_id and record.operation_id != f"backup.create:{manifest.set_id}"
        for record in manifest.contributors
    ):
        reasons.append(BackupVerificationReason.MIXED_RUN.value)

    declared_paths: set[str] = set()
    for artifact in manifest.artifacts:
        declared_paths.add(artifact.relative_path)
        raw_path = set_dir / artifact.relative_path
        if raw_path.is_symlink():
            reasons.append(BackupVerificationReason.SYMLINK.value)
            continue
        try:
            path = validate_contained_path(set_dir, artifact.relative_path)
        except BackupSetError:
            reasons.append(BackupVerificationReason.PATH_ESCAPE.value)
            continue
        if not path.is_file():
            reasons.append(BackupVerificationReason.MISSING_ARTIFACT.value)
            continue
        if path.stat().st_size != artifact.size_bytes:
            reasons.append(BackupVerificationReason.SIZE_MISMATCH.value)
        if sha256_file(path) != artifact.sha256:
            reasons.append(BackupVerificationReason.DIGEST_MISMATCH.value)

    disk_paths = {
        path.relative_to(set_dir).as_posix()
        for path in set_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    expected_paths = declared_paths | {SET_MANIFEST_NAME}
    if disk_paths - expected_paths:
        reasons.append(BackupVerificationReason.EXTRA_ARTIFACT.value)

    reasons = list(dict.fromkeys(reasons))
    if reasons:
        return _verify_failed(manifest.set_id, tuple(reasons), manifest.to_dict())
    return BackupVerifyResult(
        set_id=manifest.set_id,
        state="succeeded",
        accepted=True,
        reasons=(),
        manifest=manifest.to_dict(),
    )


def _validate_target(target: Path) -> None:
    """Refuse to touch an existing or non-empty target; a set can never be overwritten."""
    if target.exists() and any(target.iterdir()):
        raise BackupSetError(
            BackupVerificationReason.PATH_ESCAPE,
            f"backup target must be new and empty: {target}",
        )


def _validate_contributor_result(
    provider: str, result: BackupContributorResult, operation_id: str
) -> None:
    """Reject a contributor that failed, lied about identity, or left its prefix."""
    if result.state is BackupContributorState.FAILED:
        raise BackupSetError(
            BackupVerificationReason.CORRUPT_MANIFEST,
            f"contributor {provider!r} failed: {result.failure.get('reason', 'unknown') if result.failure else 'unknown'}",
        )
    if result.provider != provider:
        raise BackupSetError(
            BackupVerificationReason.MIXED_RUN,
            f"contributor returned artifacts for {result.provider!r}, expected {provider!r}",
        )
    for artifact in result.artifacts:
        expected_prefix = f"{provider}/"
        if not artifact.relative_path.startswith(expected_prefix):
            raise BackupSetError(
                BackupVerificationReason.PATH_ESCAPE,
                f"contributor {provider!r} wrote outside its owned prefix: "
                f"{artifact.relative_path!r}",
            )


def _hash_artifact(set_dir: Path, artifact: BackupArtifact) -> BackupArtifact:
    """Recompute identity from disk; the coordinator, not the provider, is authority."""
    path = validate_contained_path(set_dir, artifact.relative_path)
    if path.is_symlink() or not path.is_file():
        raise BackupSetError(
            BackupVerificationReason.MISSING_ARTIFACT,
            f"contributor declared a missing artifact: {artifact.relative_path!r}",
        )
    size = path.stat().st_size
    digest = sha256_file(path)
    if artifact.size_bytes and artifact.size_bytes != size:
        raise BackupSetError(
            BackupVerificationReason.SIZE_MISMATCH,
            f"declared size for {artifact.relative_path!r} does not match bytes on disk",
        )
    if artifact.sha256 and artifact.sha256 != digest:
        raise BackupSetError(
            BackupVerificationReason.DIGEST_MISMATCH,
            f"declared digest for {artifact.relative_path!r} does not match bytes on disk",
        )
    return BackupArtifact(
        provider=artifact.provider,
        name=artifact.name,
        relative_path=artifact.relative_path,
        size_bytes=size,
        sha256=digest,
        metadata=artifact.metadata,
    )


def _failed_result(
    *,
    journal: OperationJournalStore,
    operation_id: str,
    set_id: str,
    target: Path,
    contributor_results: Sequence[BackupContributorResult],
    failure: dict[str, Any],
) -> BackupCreateResult:
    result = BackupCreateResult(
        set_id=set_id,
        target=str(target),
        state="failed",
        accepted=False,
        manifest=None,
        contributors=tuple(record.to_dict() for record in contributor_results),
        failure=failure,
    )
    _record_result(journal, operation_id, result.to_dict())
    return result


def _record_result(
    journal: OperationJournalStore, operation_id: str, result: dict[str, Any]
) -> None:
    entry = journal.read(operation_id)
    if entry is None:
        return
    complete_operation(journal, operation_id, result)


def _ensure_failed_record(
    contributor_results: Sequence[BackupContributorResult],
    failure: dict[str, Any],
    operation_id: str,
) -> list[BackupContributorResult]:
    """Return records with every declared provider represented.

    Providers never attempted (an earlier contributor raised) are recorded as
    failed with a stable ``not_attempted`` reason so a failed result always
    accounts for the full ADR 0049 inventory.
    """
    known = {record.provider for record in contributor_results}
    extra = [
        fail_contributor(provider, "not_attempted", operation_id)
        for provider in BACKUP_PROVIDER_ORDER
        if provider not in known
    ]
    return [*contributor_results, *extra]


def _sanitize_failure(exc: Exception) -> dict[str, Any]:
    return {"reason": redact_message(str(exc) or type(exc).__name__)}


def _verify_failed(
    set_id: str, reasons: tuple[str, ...], manifest: dict[str, Any] | None
) -> BackupVerifyResult:
    return BackupVerifyResult(
        set_id=set_id,
        state="failed",
        accepted=False,
        reasons=reasons,
        manifest=manifest,
    )


def _extract_set_id(payload: Any) -> str:
    if isinstance(payload, Mapping):
        value = payload.get("set_id")
        if isinstance(value, str):
            return value
    return ""


def _result_from_dict(stored: Mapping[str, Any]) -> BackupCreateResult:
    return BackupCreateResult(
        set_id=str(stored.get("set_id", "")),
        target=str(stored.get("target", "")),
        state=str(stored.get("state", "unknown")),
        accepted=bool(stored.get("accepted")),
        manifest=stored.get("manifest"),
        contributors=tuple(stored.get("contributors") or ()),
        failure=stored.get("failure"),
    )
