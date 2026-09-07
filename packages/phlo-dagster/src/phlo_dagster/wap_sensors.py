"""Write-Audit-Publish (WAP) lifecycle sensors for Dagster.

This module implements automated WAP pattern orchestration for versioned data
catalogs (e.g., Nessie). The WAP pattern ensures data quality by isolating
writes on branches, auditing with automated checks, and only publishing
(promoting to main) after validation passes.

WAP Phases:
    1. Write: Data is written to an isolated branch (pipeline-run-{run_id})
    2. Audit: Asset checks validate data quality on the branch
    3. Publish: Successful runs are merged to main, branches cleaned up

Sensor Components:
    - wap_auto_promotion_sensor: Promotes explicitly prepared branches after successful audit
    - wap_branch_cleanup_sensor: Removes stale branches past retention

Catalog Requirements:
    Requires a catalog capability with:
    - Branch/ref support (supports_refs)
    - Branch promotion support (supports_promote)
    - VersionedCatalog interface implementation

    Typically provided by phlo-nessie package.

Configuration:
    Environment variables control sensor behavior::

    PHLO_WAP_BRANCH_CREATION_INTERVAL_SECONDS: Branch sensor poll interval (default: 30)
    PHLO_WAP_PROMOTION_INTERVAL_SECONDS: Promotion sensor poll interval (default: 60)
    PHLO_WAP_CLEANUP_INTERVAL_SECONDS: Cleanup sensor poll interval (default: 3600)

Branch Naming:
    Branches follow pattern: pipeline-run-{run_id}
    Example: pipeline-run-abc123def456

Example:
    Enabling WAP sensors in definitions::

        from phlo_dagster.wap_sensors import get_wap_definitions

        wap_defs = get_wap_definitions()
        defs = dg.Definitions.merge(your_defs, wap_defs)

Builds on the phlo capability resolver, hooks bus, and run-evidence store to drive WAP
promotion and cleanup sensors against versioned catalogs.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import dagster as dg

from phlo._correlation import ProjectIdentity, resolve_project_identity
from phlo.capabilities.interfaces import (
    RefQueryCatalogManager,
    SnapshotPromotionCatalog,
    VersionedCatalog,
)
from phlo.capabilities.resolver import resolve_capability
from phlo._attempt import attempt_from_tags
from phlo.hooks import HookCorrelation, QualityResultEvent, get_hook_bus
from phlo.logging import get_logger
from phlo.config import get_settings
from phlo.run_evidence import (
    RequiredEvidenceProfile,
    RunReconciler,
    default_run_evidence_store,
    emit_observation,
)
from phlo_dagster.run_evidence import DagsterRunEvidenceSource
from phlo_dagster.wap_launch import (
    WAP_ATTEMPT_TAG,
    WAP_BRANCH_TAG,
    WAP_PROJECT_ID_TAG,
    WAP_REF_TAG,
    WAP_RUN_ID_TAG,
    WAP_STRATEGY_BRANCH,
    WAP_STRATEGY_SNAPSHOT,
    _report_path,
    _report_snapshot_path,
    read_wap_launch_manifest,
    write_wap_report,
)

logger = get_logger(__name__)

WAP_BRANCH_PREFIX = "pipeline-"
OWNED_WAP_BRANCH_PREFIX = "pipeline-run-"
WAP_TAG_KEY = WAP_BRANCH_TAG
DEFAULT_RETENTION_HOURS = 24
DEFAULT_CLEANUP_INTERVAL_SECONDS = int(os.getenv("PHLO_WAP_CLEANUP_INTERVAL_SECONDS", "3600"))
DEFAULT_PROMOTION_INTERVAL_SECONDS = int(os.getenv("PHLO_WAP_PROMOTION_INTERVAL_SECONDS", "60"))

# The ADR-frozen blessed contribution set for the WAP profile. Provider
# contributors register declaratively (Plan 008); until all six are present,
# composition reports unavailable and the promoted run is not marked reconciled.
WAP_REQUIRED_CONTRIBUTIONS = (
    "dlt.ingest",
    "dbt.transform",
    "pandera.check",
    "iceberg.snapshot",
    "nessie.catalog",
    "dagster.terminal",
)


def _wap_evidence_profile():
    """Lazily resolve the composed WAP evidence profile through core.

    Returns the composed profile (possibly unavailable) instead of a static,
    provider-owned requirement set.
    """
    from phlo.run_evidence.profiles import resolve_composed_evidence_profile

    return resolve_composed_evidence_profile("wap", "1", WAP_REQUIRED_CONTRIBUTIONS)


def _branch_hash(catalog: VersionedCatalog, branch: str) -> str | None:
    get_branch_hash = getattr(catalog, "get_branch_hash", None)
    if not callable(get_branch_hash):
        return None
    try:
        value = get_branch_hash(branch)
    except Exception:
        logger.warning("wap_report_branch_hash_failed", branch_name=branch, exc_info=True)
        return None
    return str(value) if value else None


def _load_versioned_catalog() -> VersionedCatalog:
    """Resolve the active versioned catalog capability for WAP flows.

    Raises RuntimeError when no catalog capability is available or it does
    not support refs and promotion.

    """
    resolution = resolve_capability("catalog")
    if resolution is None:
        raise RuntimeError("WAP sensors require a catalog capability with ref/promotion support.")

    if not (resolution.support.supports_refs and resolution.support.supports_promote):
        raise RuntimeError(
            "WAP sensors require a catalog capability that supports refs and promotion."
        )

    provider = resolution.provider
    if not isinstance(provider, VersionedCatalog):
        raise RuntimeError("WAP sensors require a VersionedCatalog-compatible provider.")
    return provider


def _load_snapshot_promotion_catalog() -> SnapshotPromotionCatalog:
    """Resolve the active snapshot-promotion catalog capability for WAP flows.

    Raises RuntimeError when no catalog capability is available or it does
    not support snapshot-based release promotion.
    """
    resolution = resolve_capability("catalog")
    if resolution is None:
        raise RuntimeError("WAP sensors require a catalog capability with promotion support.")
    if not (resolution.support.supports_promote and resolution.support.supports_snapshots):
        raise RuntimeError(
            "WAP snapshot sensors require a catalog capability that supports snapshot promotion."
        )
    provider = resolution.provider
    if not isinstance(provider, SnapshotPromotionCatalog):
        raise RuntimeError(
            "WAP snapshot sensors require a SnapshotPromotionCatalog-compatible provider."
        )
    return provider


def _load_wap_catalog():
    """Resolve the catalog provider matching the configured WAP strategy."""
    from phlo.infrastructure import load_wap_config

    if load_wap_config().strategy == WAP_STRATEGY_SNAPSHOT:
        return _load_snapshot_promotion_catalog()
    return _load_versioned_catalog()


def _release_revision(catalog: Any) -> int:
    """Read the promotion catalog's release-pointer revision."""
    try:
        return int(catalog.release_revision())
    except Exception:
        logger.warning("wap_release_revision_read_failed", exc_info=True)
        return -1


def _cleanup_owned_candidates(catalog: SnapshotPromotionCatalog, namespace: str) -> bool:
    """Abort one owned candidate namespace after its release (or rejection).

    Aborting drops the run-scoped candidate refs so the staged snapshots can
    no longer be promoted; on success the release pointer is what consumers
    resolve. Providers must make abort idempotent for the retry path.
    """
    if not _is_owned_wap_branch(namespace):
        logger.warning("wap_candidate_cleanup_rejected_unowned_ref", namespace=namespace)
        return False
    try:
        return bool(catalog.abort_candidates(namespace=namespace))
    except Exception:
        logger.warning("wap_candidate_cleanup_failed", namespace=namespace, exc_info=True)
        return False


def _load_ref_query_catalog_manager() -> RefQueryCatalogManager | None:
    """Resolve the optional query-catalog cleanup capability for WAP refs.

    The configured query-engine provider may also own a file-backed catalog for
    each WAP ref. Providers that do not implement this optional capability keep
    the established Nessie-only branch cleanup behaviour.
    """
    resolution = resolve_capability("query_engine")
    if resolution is None or not isinstance(resolution.provider, RefQueryCatalogManager):
        return None
    return resolution.provider


def _is_owned_wap_branch(branch_name: str) -> bool:
    """Return whether a ref was created by Phlo's WAP branch lifecycle."""
    return branch_name.startswith(OWNED_WAP_BRANCH_PREFIX)


def _cleanup_owned_wap_branch(
    catalog: VersionedCatalog,
    branch_name: str,
    query_catalog_manager: RefQueryCatalogManager | None,
) -> bool:
    """Clean up one owned WAP ref and its optional query catalog.

    The query catalog is removed first so a manager failure leaves the Nessie
    branch available for a truthful retry. If branch deletion then fails, the
    operation is still incomplete; providers must make their catalog removal
    idempotent for the retry path.
    """
    if not _is_owned_wap_branch(branch_name):
        logger.warning("wap_branch_cleanup_rejected_unowned_ref", branch_name=branch_name)
        return False

    if query_catalog_manager is not None:
        try:
            query_catalog_manager.drop_ref_query_catalog(branch_name)
        except Exception:
            logger.warning(
                "wap_query_catalog_cleanup_failed",
                branch_name=branch_name,
                exc_info=True,
            )
            return False

    try:
        return catalog.delete_branch(branch_name)
    except Exception:
        logger.warning("wap_branch_cleanup_failed", branch_name=branch_name, exc_info=True)
        return False


def _wap_branch_name(run_id: str) -> str:
    """Derive the WAP branch name for a Dagster run ID."""
    return f"{WAP_BRANCH_PREFIX}run-{run_id}"


def _project_identity_for_run(run: Any) -> ProjectIdentity:
    """Resolve run tags against the configured single-project identity."""
    return resolve_project_identity(
        getattr(run, "tags", {}) or {},
        get_settings().phlo_project,
    )


def _project_id_for_run(run: Any) -> str | None:
    return _project_identity_for_run(run).project_id


def _attempt_for_run(run: Any) -> int | None:
    """Return a positive attempt or None so missing correlation is observable."""
    attempt, _error = attempt_from_tags(getattr(run, "tags", {}) or {})
    return attempt


def _read_wap_report(run_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(_report_path(run_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _severity_label(value: Any) -> str:
    """Normalize a Dagster severity (enum or plain string) to lowercase text.

    ``str(AssetCheckSeverity.WARN)`` yields ``"AssetCheckSeverity.WARN"``, not
    ``"WARN"``, so enum values must be unwrapped via ``.value`` before any
    comparison against the plain ``"warn"``/``"error"`` labels.
    """
    raw = getattr(value, "value", value)
    return str(raw or "error").lower()


def _check_is_blocking(value: Any) -> bool:
    """Whether a failed check blocks WAP promotion (single severity rule).

    Only explicit warnings are non-blocking; anything else fails closed
    through the neutral severity contract (error/critical block).
    """
    from phlo.capabilities.specs import CheckSeverity, is_blocking_severity

    label = _severity_label(value)
    mapping = {
        "warn": CheckSeverity.WARNING,
        "warning": CheckSeverity.WARNING,
        "error": CheckSeverity.ERROR,
        "critical": CheckSeverity.CRITICAL,
        "severe": CheckSeverity.ERROR,
    }
    severity = mapping.get(label, CheckSeverity.ERROR)
    return is_blocking_severity(severity)


def _quality_check_records(instance: Any, run_id: str) -> list[dict[str, Any]] | None:
    """Return durable check outcomes with severity and blocking classification.

    Severity defaults to ``error`` and blocking to ``True`` when the recorded
    evaluation predates those fields, so legacy evidence classifies as
    blocking (fail-closed).
    """
    try:
        check_records = instance.get_records_for_run(
            run_id,
            of_type=dg.DagsterEventType.ASSET_CHECK_EVALUATION,
        )
    except Exception:
        return None
    checks: list[dict[str, Any]] = []
    for record in getattr(check_records, "records", ()):
        entry = getattr(record, "event_log_entry", None)
        evaluation = getattr(entry, "asset_check_evaluation", None)
        if evaluation is None:
            continue
        storage_id = getattr(record, "storage_id", None) or getattr(entry, "storage_id", None)
        checks.append(
            {
                "event_id": f"dagster-quality:{storage_id}" if storage_id is not None else None,
                "passed": bool(getattr(evaluation, "passed", False)),
                "severity": _severity_label(getattr(evaluation, "severity", None)),
                "blocking": bool(getattr(evaluation, "blocking", True)),
            }
        )
    return checks


def _persist_aggregate_quality_decision(
    *, project_id: str, run_id: str, attempt: int, checks: list[dict[str, Any]]
) -> str | None:
    """Persist and return the durable aggregate quality-result identity.

    An empty check list persists as a vacuous pass: assets without declared
    checks (Sling replications, for example) must still produce durable
    promotion evidence, matching ``_all_checks_passed`` semantics where zero
    executed checks counts as passed. A None return is reserved for unusable
    input - any recorded check missing its durable event id.

    Failed WARN-severity checks are non-blocking: the aggregate still passes,
    but as ``passed_with_warnings`` with severity ``warn`` so downstream
    evidence consumers can surface the warnings without reading a rejection.
    """
    if any(not check.get("event_id") for check in checks):
        return None
    error_failures = [
        c for c in checks if not c["passed"] and _check_is_blocking(c.get("severity"))
    ]
    warn_failures = [
        c for c in checks if not c["passed"] and not _check_is_blocking(c.get("severity"))
    ]
    passed = not error_failures
    severity = "error" if error_failures else ("warn" if warn_failures else None)
    decision = (
        "rejected" if error_failures else ("passed_with_warnings" if warn_failures else "passed")
    )
    event_id = (
        "wap-quality-"
        + hashlib.sha256(
            json.dumps(
                {"run_id": run_id, "attempt": attempt, "checks": checks},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:32]
    )
    event = QualityResultEvent(
        event_type="quality.result",
        event_id=event_id,
        producer="phlo-dagster-nessie",
        asset_key="__pipeline__",
        check_name="wap.aggregate",
        passed=passed,
        severity=severity,
        check_type="aggregate",
        metadata={
            "decision": decision,
            "failed_check_ids": [c["event_id"] for c in error_failures if c["event_id"]],
            "warned_check_ids": [c["event_id"] for c in warn_failures if c["event_id"]],
            "checks": checks,
        },
        correlation=HookCorrelation(
            project_id=project_id,
            run_id=run_id,
            attempt=attempt,
        ),
    )
    try:
        get_hook_bus().emit(event)
        results = default_run_evidence_store().list_quality_results(
            project_id, run_id, attempt=attempt
        )
    except Exception:
        logger.warning(
            "wap_aggregate_quality_evidence_persist_failed", run_id=run_id, exc_info=True
        )
        return None
    for result in results:
        metadata = result.get("metadata") or {}
        if (
            result.get("check_id") == "wap.aggregate"
            and bool(result.get("passed")) == passed
            and metadata.get("checks") == checks
        ):
            return str(result["quality_result_id"])
    return None


def _quality_evidence(
    run_id: str,
    instance: Any | None = None,
    *,
    project_id: str | None = None,
    attempt: int | None = None,
    evidence_run_id: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Read report evidence and bind promotion to a durable aggregate decision."""
    path = _report_path(run_id)
    raw: bytes | None = None
    try:
        raw = path.read_bytes()
        report = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        report = None
    if report is not None and not isinstance(report, dict):
        report = None
    if report is not None and report.get("run_id") != run_id:
        return None, {"quality_evidence": {"status": "unavailable"}}
    quality_id = None
    checks = _quality_check_records(instance, run_id) if instance is not None else None
    failed_check_ids = [
        c["event_id"]
        for c in checks or []
        if not c["passed"] and c["event_id"] and str(c.get("severity", "error")).lower() != "warn"
    ]
    warned_check_ids = [
        c["event_id"]
        for c in checks or []
        if not c["passed"] and c["event_id"] and str(c.get("severity", "error")).lower() == "warn"
    ]
    if checks is not None and project_id and attempt is not None:
        aggregate_id = _persist_aggregate_quality_decision(
            project_id=project_id,
            run_id=evidence_run_id or run_id,
            attempt=attempt,
            checks=checks,
        )
        if aggregate_id is not None:
            quality_id = aggregate_id
    checksum = hashlib.sha256(raw).hexdigest() if raw is not None else None
    snapshot_path = _report_snapshot_path(run_id, checksum) if checksum is not None else None
    evidence_path = snapshot_path if snapshot_path is not None and snapshot_path.exists() else path
    decision = (
        "rejected"
        if failed_check_ids
        else "passed_with_warnings"
        if warned_check_ids
        else "passed"
        if checks is not None
        else "unavailable"
    )
    return quality_id, {
        "quality_evidence": {
            "uri": str(evidence_path) if raw is not None else None,
            "checksum": checksum,
            "status": "observed" if quality_id else "unavailable",
            "identifier_source": (
                "durable_aggregate_quality_result"
                if quality_id and checks is not None and project_id and attempt is not None
                else None
            ),
            "decision_scope": "aggregate" if checks is not None else "unavailable",
            "decision": decision,
            "failed_check_ids": failed_check_ids,
            "warned_check_ids": warned_check_ids,
        }
    }


def _record_uncorrelated_gap(run_id: str, *, branch: str, missing: list[str], reason: str) -> None:
    """Persist cleanup evidence without letting it masquerade as pipeline evidence."""
    write_wap_report(
        run_id,
        status="incomplete",
        branch=branch,
        observation_scope="uncorrelated_maintenance",
        evidence_completeness="incomplete",
        missing_evidence=missing,
        maintenance_observation={"operation": "cleanup", "reason": reason},
    )
    logger.warning(
        "wap_uncorrelated_maintenance_evidence_gap",
        run_id=run_id,
        branch_name=branch,
        missing_evidence=missing,
    )


def _normalized_dagster_status(run: Any) -> str | None:
    raw_status = getattr(run, "status", None)
    value = getattr(raw_status, "value", raw_status)
    normalized = str(value).rsplit(".", 1)[-1].lower() if value is not None else ""
    return {
        "success": "success",
        "failure": "failed",
        "failed": "failed",
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "skipped": "skipped",
    }.get(normalized)


def _logical_run_id(run: Any) -> str:
    """Return the report identity, falling back to the physical Dagster ID."""
    dagster_run_id = getattr(run, "run_id", None)
    if not dagster_run_id:
        return ""
    tags = getattr(run, "tags", {}) or {}
    logical_run_id = tags.get("phlo/run_id") if isinstance(tags, dict) else None
    return str(logical_run_id or dagster_run_id)


def _verify_wap_launch_manifest(run: Any, branch_name: str) -> tuple[str, dict[str, Any]] | None:
    """Fail closed unless the run still matches its pre-launch WAP manifest."""
    logical_run_id = _logical_run_id(run)
    dagster_run_id = str(getattr(run, "run_id", "") or "")
    tags = getattr(run, "tags", {}) or {}
    project = _project_identity_for_run(run)
    attempt, attempt_error = attempt_from_tags(tags)
    if (
        not project.project_id
        or attempt is None
        or attempt_error
        or tags.get(WAP_PROJECT_ID_TAG) != project.project_id
        or tags.get(WAP_ATTEMPT_TAG) != str(attempt)
    ):
        return None
    expected_tags = {
        WAP_RUN_ID_TAG: logical_run_id,
        WAP_BRANCH_TAG: branch_name,
        WAP_REF_TAG: branch_name,
        WAP_PROJECT_ID_TAG: project.project_id,
        WAP_ATTEMPT_TAG: str(attempt),
    }
    if (
        not logical_run_id
        or not dagster_run_id
        or any(tags.get(key) != value for key, value in expected_tags.items())
    ):
        return None
    manifest = _read_wap_report(logical_run_id)
    checksum = manifest.get("launch_manifest_checksum") if manifest else None
    binding = read_wap_launch_manifest(logical_run_id, str(checksum)) if checksum else None
    if not manifest or not binding:
        return None
    has_launch_source_hash = "launch_source_hash" in manifest
    has_launch_target_hash_before = "launch_target_hash_before" in manifest
    if not has_launch_source_hash and not has_launch_target_hash_before:
        # Reports written before launch hashes had their own fields used the
        # mutable lifecycle hashes.  Recover those facts from the verified,
        # content-addressed binding once so retries do not reinterpret a
        # later branch head as launch state.
        launch_source_hash = binding.get("source_hash")
        launch_target_hash_before = binding.get("target_hash_before")
        if not write_wap_report(
            logical_run_id,
            launch_source_hash=launch_source_hash,
            launch_target_hash_before=launch_target_hash_before,
        ):
            return None
        manifest = _read_wap_report(logical_run_id)
        if not manifest:
            return None
    elif not has_launch_source_hash or not has_launch_target_hash_before:
        return None
    else:
        launch_source_hash = manifest["launch_source_hash"]
        launch_target_hash_before = manifest["launch_target_hash_before"]
    if (
        manifest.get("run_id") != logical_run_id
        or manifest.get("branch") != branch_name
        or manifest.get("dagster_run_id") != dagster_run_id
        or manifest.get("launch_tags") != expected_tags
        or binding
        != {
            "schema_version": "phlo.wap_launch_manifest.v1",
            "logical_run_id": logical_run_id,
            "dagster_run_id": dagster_run_id,
            "branch": branch_name,
            "tags": expected_tags,
            "source_hash": launch_source_hash,
            "target_branch": "main",
            "target_hash_before": launch_target_hash_before,
        }
    ):
        return None
    return logical_run_id, manifest


def _reconcile_promoted_wap_run(run: Any, instance: Any) -> bool:
    """Persist Dagster's authoritative history under the WAP logical identity."""
    dagster_run_id = getattr(run, "run_id", None)
    project_id = _project_id_for_run(run)
    if not dagster_run_id or not project_id:
        return False
    try:
        composed = _wap_evidence_profile()
        if not composed.available:
            logger.warning(
                "wap_promoted_run_evidence_profile_unavailable",
                dagster_run_id=dagster_run_id,
                missing=composed.missing_contribution_ids,
            )
            # Fall back to a minimal profile when provider contributions
            # are not yet registered (intermediate stacked-PR state).  Once
            # all six contributions are declared (Plan 008), the composed
            # profile is used instead.
            profile = RequiredEvidenceProfile(profile_id="wap", version="1", provider="dagster")
        else:
            profile = composed.profile
        RunReconciler(
            default_run_evidence_store(),
            DagsterRunEvidenceSource(instance, project_id=project_id),
        ).reconcile(project_id, dagster_run_id, profile)
        return True
    except Exception:
        logger.warning(
            "wap_promoted_run_reconciliation_failed",
            dagster_run_id=dagster_run_id,
            logical_run_id=_logical_run_id(run),
            exc_info=True,
        )
        return False


def _emit_wap_observation(
    *,
    run: Any,
    status: str,
    run_status: str | None = None,
    operation: str,
    catalog_ref: str,
    source_hash: str | None = None,
    target_hash: str | None = None,
    merge_outcome: str | None = None,
    quality_decision_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    project_id = _project_id_for_run(run)
    project_identity = _project_identity_for_run(run)
    attempt = _attempt_for_run(run)
    run_id = _logical_run_id(run)
    if not run_id:
        return
    if not project_id or attempt is None:
        _record_uncorrelated_gap(
            run_id,
            branch=catalog_ref,
            missing=[
                field
                for field, value in (
                    (project_identity.error or "project_id", project_id),
                    ("attempt", attempt),
                )
                if not value
            ],
            reason="missing_run_correlation",
        )
        return
    emit_observation(
        project_id=project_id,
        run_id=run_id,
        attempt=attempt,
        observation_type="publish",
        status=status,
        run_status=run_status,
        producer="phlo-dagster-nessie",
        catalog_change={
            "operation": operation,
            "catalog_ref": catalog_ref,
            "resource_identity": {
                "resource_type": "catalog_ref",
                "resource_id": catalog_ref,
                "tenant": project_id,
                "attributes": {
                    key: value
                    for key, value in {
                        "operation": operation,
                        "source_hash": source_hash,
                        "target_hash": target_hash,
                        "merge_outcome": merge_outcome,
                    }.items()
                    if isinstance(value, str) and value
                },
            },
            "source_hash": source_hash,
            "target_hash": target_hash,
            "merge_outcome": merge_outcome,
            "quality_decision_id": quality_decision_id,
            "metadata": metadata or {},
        },
        identity_parts=(operation, catalog_ref, source_hash, target_hash, merge_outcome),
    )


# ---------------------------------------------------------------------------
# Sensor 1: Auto-promotion (audit → publish)
# ---------------------------------------------------------------------------


def _release_resolved_for_run(
    catalog: SnapshotPromotionCatalog, namespace: str, release_id: str
) -> bool:
    """Return whether every candidate in ``namespace`` resolved to our release."""
    try:
        candidates = catalog.list_candidates(namespace=namespace)
    except Exception:
        return False
    if not candidates:
        return False
    for candidate in candidates:
        try:
            record = catalog.resolve_release(table_name=candidate.table_name)
        except Exception:
            return False
        if record is None or record.release_id != release_id:
            return False
    return True


def _advance_snapshot_promotion(
    *,
    catalog: SnapshotPromotionCatalog,
    run: Any,
    branch_name: str,
    logical_run_id: str,
    prior_report: dict[str, Any] | None,
    quality_decision_id: str | None,
    quality_metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """Advance one snapshot-strategy run through promote → candidate abort.

    Mirrors the branch outbox: persist intent before crossing the catalog
    boundary, promote with a compare-and-swap guard on the release pointer,
    and checkpoint candidate cleanup separately. Returns the promotion state
    for the shared finalize tail, or None when the run was terminalized here
    (the caller must skip to its next run).
    """
    already_merged = prior_report is not None and prior_report.get("merge_state") == "merged"
    merge_started = prior_report is not None and prior_report.get("merge_state") == "merge_started"
    current_revision = _release_revision(catalog)
    target_hash_before = str(current_revision) if current_revision >= 0 else None
    try:
        candidates = catalog.list_candidates(namespace=branch_name)
    except Exception:
        logger.warning(
            "wap_candidate_listing_failed",
            run_id=run.run_id,
            branch_name=branch_name,
            exc_info=True,
        )
        candidates = []
    candidate_rows = [
        {"table": candidate.table_name, "snapshot_id": str(candidate.snapshot_id)}
        for candidate in candidates
    ]
    # The audited evidence is the exact set of candidate snapshot IDs the
    # release pointer is advanced to; join deterministically for the record.
    source_hash = ",".join(sorted(row["snapshot_id"] for row in candidate_rows)) or None

    resumed = already_merged or (
        merge_started
        and prior_report is not None
        and prior_report.get("target_hash_before") != target_hash_before
        and _release_resolved_for_run(catalog, branch_name, logical_run_id)
    )
    if (
        merge_started
        and not resumed
        and prior_report is not None
        and prior_report.get("target_hash_before") != target_hash_before
    ):
        # The release pointer moved after our durable intent and our release
        # did not resolve: someone else published. Refuse to guess.
        write_wap_report(
            logical_run_id,
            status="promotion_failed",
            branch=branch_name,
            source_hash=source_hash,
            target_branch="main",
            target_hash_before=prior_report.get("target_hash_before"),
            failure_reason="release_pointer_conflict",
        )
        _emit_wap_observation(
            run=run,
            status="failed",
            run_status="success",
            operation="promotion",
            catalog_ref="main",
            source_hash=source_hash,
            target_hash=prior_report.get("target_hash_before"),
            merge_outcome="failed",
            quality_decision_id=quality_decision_id,
            metadata={
                **quality_metadata,
                "changed_content_keys": {"status": "unavailable"},
            },
        )
        logger.error(
            "wap_promotion_release_conflict",
            run_id=run.run_id,
            branch_name=branch_name,
        )
        return None

    if not resumed:
        launch_revision = (prior_report or {}).get("launch_target_hash_before")
        try:
            expected_revision = int(launch_revision) if launch_revision is not None else -1
        except (TypeError, ValueError):
            expected_revision = -1
        if expected_revision < 0 or expected_revision != current_revision:
            write_wap_report(
                logical_run_id,
                status="promotion_failed",
                branch=branch_name,
                failure_reason="release_pointer_conflict",
            )
            return None
        if not write_wap_report(
            logical_run_id,
            status="promotion_pending",
            merge_state="merge_started",
            branch=branch_name,
            source_hash=source_hash,
            target_branch="main",
            target_hash_before=target_hash_before,
            candidates=candidate_rows,
        ):
            logger.warning("wap_promotion_outbox_write_failed", run_id=run.run_id)
            return None
        try:
            promoted_records = catalog.promote_candidates(
                namespace=branch_name,
                release_id=logical_run_id,
                expected_revision=expected_revision,
            )
            merged = bool(promoted_records)
        except Exception:
            logger.warning(
                "wap_promotion_raise_failed",
                run_id=run.run_id,
                branch_name=branch_name,
                exc_info=True,
            )
            merged = False
        if not merged:
            write_wap_report(
                logical_run_id,
                status="promotion_failed",
                branch=branch_name,
                source_hash=source_hash,
                target_branch="main",
                target_hash_before=target_hash_before,
                failure_reason="release_promotion_failed",
            )
            _emit_wap_observation(
                run=run,
                status="failed",
                run_status="success",
                operation="promotion",
                catalog_ref="main",
                source_hash=source_hash,
                target_hash=target_hash_before,
                merge_outcome="failed",
                quality_decision_id=quality_decision_id,
                metadata={
                    **quality_metadata,
                    "changed_content_keys": {"status": "unavailable"},
                },
            )
            logger.error(
                "wap_promotion_merge_failed",
                run_id=run.run_id,
                branch_name=branch_name,
            )
            return None
        target_hash_after = str(_release_revision(catalog))
        # This acknowledged transition is what makes a subsequent sensor
        # evaluation replay evidence/cleanup rather than promote again.
        if not write_wap_report(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch="main",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            release_id=logical_run_id,
            candidates=candidate_rows,
        ):
            logger.warning("wap_promotion_merge_receipt_write_failed", run_id=run.run_id)
            return None
    else:
        target_hash_after = str(_release_revision(catalog))

    source_deleted = bool(prior_report and prior_report.get("source_deleted"))
    if not source_deleted:
        source_deleted = _cleanup_owned_candidates(catalog, branch_name)
    if not source_deleted:
        write_wap_report(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch="main",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=False,
        )
        logger.warning("wap_promotion_cleanup_pending", run_id=run.run_id, branch_name=branch_name)
        return None
    # Checkpoint cleanup independently of the terminal report.  A retry
    # after reconciliation or tag failure must not try to abort it again.
    if not write_wap_report(
        logical_run_id,
        status="promotion_pending",
        merge_state="merged",
        branch=branch_name,
        source_hash=source_hash,
        target_branch="main",
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        source_deleted=True,
    ):
        logger.warning("wap_promotion_cleanup_receipt_write_failed", run_id=run.run_id)
        return None
    return {
        "source_hash": source_hash,
        "target_hash_before": target_hash_before,
        "target_hash_after": target_hash_after,
        "source_deleted": True,
    }


def _finalize_wap_promotion(
    run: Any,
    instance: Any,
    *,
    logical_run_id: str,
    branch_name: str,
    source_hash: str | None,
    target_hash_before: str | None,
    target_hash_after: str | None,
    source_deleted: bool,
    target_catalog_ref: str,
) -> bool:
    """Write the terminal promotion report, mark the run, and emit evidence.

    Shared by both WAP strategies; returns False when a durable write failed
    so the caller retries on a later tick instead of guessing completion.
    """
    if not write_wap_report(
        logical_run_id,
        status="promoted",
        merge_state="merged",
        branch=branch_name,
        source_hash=source_hash,
        target_branch="main",
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        source_deleted=source_deleted,
    ):
        logger.warning("wap_promotion_terminal_evidence_write_failed", run_id=run.run_id)
        return False
    instance.add_run_tags(run.run_id, {"phlo/wap_promoted": "true"})
    quality_decision_id, quality_metadata = _quality_evidence(
        run.run_id,
        instance,
        project_id=_project_id_for_run(run),
        attempt=_attempt_for_run(run),
        evidence_run_id=_logical_run_id(run),
    )
    _emit_wap_observation(
        run=run,
        status="success",
        run_status="success",
        operation="promotion",
        catalog_ref=target_catalog_ref,
        source_hash=source_hash,
        target_hash=target_hash_after,
        merge_outcome="promoted",
        quality_decision_id=quality_decision_id,
        metadata={
            **quality_metadata,
            "changed_content_keys": {"status": "unavailable"},
            "commit": {"status": "unavailable"},
        },
    )
    _emit_wap_observation(
        run=run,
        status="success" if source_deleted else "incomplete",
        run_status="success",
        operation="cleanup",
        catalog_ref=branch_name,
        source_hash=source_hash,
        merge_outcome="deleted" if source_deleted else "failed",
        metadata={"target_ref": target_catalog_ref},
    )
    return True


@dg.sensor(
    name="wap_auto_promotion_sensor",
    description="Merges WAP branches to main when all asset checks pass (WAP publish phase)",
    minimum_interval_seconds=DEFAULT_PROMOTION_INTERVAL_SECONDS,
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def wap_auto_promotion_sensor(context: dg.SensorEvaluationContext):
    """Merge pipeline branches whose runs succeeded with all checks passing.

    Scans terminal runs tagged with a WAP branch. Failed and cancelled runs
    are terminalized in their durable reports but retain their branches and
    optional query catalogs for audit until the cleanup sensor's retention
    policy applies. For successful runs, verifies that no asset checks failed,
    then merges the branch to main and cleans up. Failures are logged as
    warnings.

    """
    instance = context.instance
    from phlo.infrastructure import load_wap_config

    configured_strategy = load_wap_config().strategy
    catalog = _load_wap_catalog()
    query_catalog_manager = (
        None if configured_strategy == WAP_STRATEGY_SNAPSHOT else _load_ref_query_catalog_manager()
    )

    evaluation_time = datetime.now(timezone.utc)
    cursor_ts = None
    if context.cursor:
        try:
            cursor_ts = datetime.fromisoformat(context.cursor)
        except ValueError:
            cursor_ts = None

    # Rewind the cursor slightly so runs updated between the query and the
    # cursor commit are not skipped; a cold start (no cursor) looks back an
    # hour instead of scanning all history.
    cutoff = (
        (cursor_ts - timedelta(minutes=5)) if cursor_ts else (evaluation_time - timedelta(hours=1))
    )

    terminal_runs = list(
        instance.get_runs(
            filters=dg.RunsFilter(
                statuses=[
                    dg.DagsterRunStatus.SUCCESS,
                    dg.DagsterRunStatus.FAILURE,
                    dg.DagsterRunStatus.CANCELED,
                ],
                updated_after=cutoff,
            )
        )
    )

    promoted = 0
    blocked = 0

    for run in terminal_runs:
        run_tags = run.tags or {}
        branch_name = run_tags.get(WAP_TAG_KEY)
        if not branch_name:
            continue

        if not _is_owned_wap_branch(branch_name):
            logger.warning(
                "wap_promotion_skipped_unowned_ref",
                run_id=run.run_id,
                branch_name=branch_name,
            )
            continue

        # Written only after the full promote-and-cleanup sequence succeeds,
        # so its presence means later ticks must not reprocess this run.
        if run_tags.get("phlo/wap_promoted"):
            continue

        manifest = _verify_wap_launch_manifest(run, branch_name)
        if manifest is None:
            logical_run_id = _logical_run_id(run)
            if logical_run_id:
                write_wap_report(
                    logical_run_id,
                    status="promotion_blocked",
                    branch=branch_name,
                    failure_reason="launch_manifest_or_immutable_tags_invalid",
                )
            logger.warning(
                "wap_promotion_blocked_launch_manifest_invalid",
                run_id=run.run_id,
                branch_name=branch_name,
            )
            blocked += 1
            continue

        manifest_logical_run_id, manifest_payload = manifest
        # Fail closed on configuration drift: a run launched under a different
        # strategy must never be advanced with the wrong catalog contract.
        report_strategy = manifest_payload.get("strategy", WAP_STRATEGY_BRANCH)
        if report_strategy != configured_strategy:
            write_wap_report(
                manifest_logical_run_id,
                status="promotion_blocked",
                branch=branch_name,
                failure_reason="wap_strategy_mismatch",
            )
            logger.warning(
                "wap_promotion_blocked_strategy_mismatch",
                run_id=run.run_id,
                branch_name=branch_name,
                report_strategy=report_strategy,
                configured_strategy=configured_strategy,
            )
            blocked += 1
            continue

        run_status = _normalized_dagster_status(run)
        if run_status in {"failed", "cancelled"}:
            # Failed WAP runs are audit artifacts, like quality-rejected
            # runs. The cleanup sensor owns their eventual removal after its
            # retention period; promotion must never clean them up eagerly.
            if not write_wap_report(
                _logical_run_id(run),
                status=run_status,
                branch=branch_name,
                dagster_run_id=run.run_id,
                failure_reason=f"dagster_run_{run_status}",
            ):
                logger.warning("wap_terminal_run_report_write_failed", run_id=run.run_id)
                continue
            blocked += 1
            logger.info(
                "wap_promotion_skipped_terminal_failed_run_branch_retained",
                run_id=run.run_id,
                branch_name=branch_name,
                run_status=run_status,
            )
            continue

        if not _all_checks_passed(instance, run.run_id):
            quality_decision_id, quality_metadata = _quality_evidence(
                run.run_id,
                instance,
                project_id=_project_id_for_run(run),
                attempt=_attempt_for_run(run),
                evidence_run_id=_logical_run_id(run),
            )
            if quality_decision_id is None:
                write_wap_report(
                    _logical_run_id(run),
                    status="promotion_blocked",
                    branch=branch_name,
                    target_branch="main",
                    failure_reason="quality_evidence_unavailable",
                )
                _emit_wap_observation(
                    run=run,
                    status="incomplete",
                    run_status="success",
                    operation="promotion",
                    catalog_ref="main",
                    source_hash=_branch_hash(catalog, branch_name),
                    target_hash=_branch_hash(catalog, "main"),
                    merge_outcome="skipped_quality_evidence_unavailable",
                    metadata={
                        **quality_metadata,
                        "changed_content_keys": {"status": "unavailable"},
                    },
                )
                blocked += 1
                continue
            write_wap_report(
                _logical_run_id(run),
                status="promotion_blocked",
                branch=branch_name,
                source_hash=_branch_hash(catalog, branch_name),
                target_branch="main",
                target_hash_before=_branch_hash(catalog, "main"),
                failure_reason="asset_checks_failed",
            )
            _emit_wap_observation(
                run=run,
                status="rejected",
                run_status="success",
                operation="promotion",
                catalog_ref="main",
                source_hash=_branch_hash(catalog, branch_name),
                target_hash=_branch_hash(catalog, "main"),
                merge_outcome="rejected_quality",
                quality_decision_id=quality_decision_id,
                metadata={
                    **quality_metadata,
                    "changed_content_keys": {"status": "unavailable"},
                },
            )
            blocked += 1
            logger.info(
                "wap_promotion_blocked_quality_branch_retained",
                run_id=run.run_id,
                branch_name=branch_name,
            )
            continue

        quality_decision_id, quality_metadata = _quality_evidence(
            run.run_id,
            instance,
            project_id=_project_id_for_run(run),
            attempt=_attempt_for_run(run),
            evidence_run_id=_logical_run_id(run),
        )
        if quality_decision_id is None:
            write_wap_report(
                _logical_run_id(run),
                status="promotion_blocked",
                branch=branch_name,
                target_branch="main",
                failure_reason="quality_evidence_unavailable",
            )
            _emit_wap_observation(
                run=run,
                status="incomplete",
                run_status="success",
                operation="promotion",
                catalog_ref="main",
                source_hash=_branch_hash(catalog, branch_name),
                target_hash=_branch_hash(catalog, "main"),
                merge_outcome="skipped_quality_evidence_unavailable",
                metadata={**quality_metadata, "changed_content_keys": {"status": "unavailable"}},
            )
            blocked += 1
            continue

        logical_run_id = _logical_run_id(run)
        prior_report = _read_wap_report(logical_run_id)
        if report_strategy == WAP_STRATEGY_SNAPSHOT:
            advance = _advance_snapshot_promotion(
                catalog=catalog,
                run=run,
                branch_name=branch_name,
                logical_run_id=logical_run_id,
                prior_report=prior_report,
                quality_decision_id=quality_decision_id,
                quality_metadata=quality_metadata,
            )
            if advance is None:
                continue
            if _finalize_wap_promotion(
                run,
                instance,
                logical_run_id=logical_run_id,
                branch_name=branch_name,
                source_hash=advance["source_hash"],
                target_hash_before=advance["target_hash_before"],
                target_hash_after=advance["target_hash_after"],
                source_deleted=advance["source_deleted"],
                target_catalog_ref=f"release:{logical_run_id}",
            ):
                promoted += 1
                logger.info(
                    "wap_candidate_promoted",
                    run_id=run.run_id,
                    branch_name=branch_name,
                )
            continue
        source_hash = _branch_hash(catalog, branch_name)
        target_hash_before = _branch_hash(catalog, "main")
        already_merged = prior_report is not None and prior_report.get("merge_state") == "merged"
        merge_started = (
            prior_report is not None and prior_report.get("merge_state") == "merge_started"
        )
        if already_merged and prior_report is not None:
            source_hash = prior_report.get("source_hash") or source_hash
            target_hash_before = prior_report.get("target_hash_before") or target_hash_before
            merged = True
        elif merge_started:
            # Intent alone cannot distinguish a rejected merge from a committed
            # merge whose acknowledgement was lost. Target movement may belong
            # to another writer; retain the source until a receipt is available.
            write_wap_report(
                logical_run_id,
                status="promotion_pending",
                failure_reason="merge_outcome_unknown",
            )
            logger.warning(
                "wap_promotion_merge_recovery_required",
                run_id=run.run_id,
                branch_name=branch_name,
            )
            continue
        else:
            # Persist intent before crossing the catalog boundary.  This is a
            # retry record, not a terminal promotion marker.
            if not write_wap_report(
                logical_run_id,
                status="promotion_pending",
                merge_state="merge_started",
                branch=branch_name,
                source_hash=source_hash,
                target_branch="main",
                target_hash_before=target_hash_before,
            ):
                logger.warning("wap_promotion_outbox_write_failed", run_id=run.run_id)
                continue
            merged = catalog.merge_branch(source=branch_name, target="main")
        if not merged:
            write_wap_report(
                logical_run_id,
                status="promotion_failed",
                merge_state="merge_failed",
                branch=branch_name,
                source_hash=source_hash,
                target_branch="main",
                target_hash_before=target_hash_before,
                failure_reason="merge_branch_returned_false",
            )
            quality_decision_id, quality_metadata = _quality_evidence(
                run.run_id,
                instance,
                project_id=_project_id_for_run(run),
                attempt=_attempt_for_run(run),
                evidence_run_id=_logical_run_id(run),
            )
            _emit_wap_observation(
                run=run,
                status="failed",
                run_status="success",
                operation="promotion",
                catalog_ref="main",
                source_hash=source_hash,
                target_hash=target_hash_before,
                merge_outcome="failed",
                quality_decision_id=quality_decision_id,
                metadata={
                    **quality_metadata,
                    "changed_content_keys": {"status": "unavailable"},
                },
            )
            logger.error(
                "wap_promotion_merge_failed",
                run_id=run.run_id,
                branch_name=branch_name,
            )
            continue

        target_hash_after = _branch_hash(catalog, "main")
        # This acknowledged transition is what makes a subsequent sensor
        # evaluation replay evidence/cleanup rather than invoke merge again.
        if not already_merged and not write_wap_report(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch="main",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
        ):
            logger.warning("wap_promotion_merge_receipt_write_failed", run_id=run.run_id)
            continue
        source_deleted = bool(prior_report and prior_report.get("source_deleted"))
        if already_merged and not source_deleted:
            # A deleted ref is an idempotent cleanup success.  In particular,
            # do not turn a crash after cleanup into an endless retry because
            # providers correctly reject deletion of an absent branch.
            source_deleted = _branch_hash(catalog, branch_name) is None
        if not source_deleted:
            source_deleted = _cleanup_owned_wap_branch(
                catalog,
                branch_name,
                query_catalog_manager,
            )
        if not source_deleted:
            write_wap_report(
                logical_run_id,
                status="promotion_pending",
                merge_state="merged",
                branch=branch_name,
                source_hash=source_hash,
                target_branch="main",
                target_hash_before=target_hash_before,
                target_hash_after=target_hash_after,
                source_deleted=False,
            )
            logger.warning(
                "wap_promotion_cleanup_pending", run_id=run.run_id, branch_name=branch_name
            )
            continue
        # Checkpoint cleanup independently of the terminal report.  A retry
        # after reconciliation or tag failure must not try to delete it again.
        if not write_wap_report(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch="main",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=True,
        ):
            logger.warning("wap_promotion_cleanup_receipt_write_failed", run_id=run.run_id)
            continue
        if not _reconcile_promoted_wap_run(run, instance):
            logger.warning("wap_promotion_reconciliation_pending", run_id=run.run_id)
            continue
        if _finalize_wap_promotion(
            run,
            instance,
            logical_run_id=logical_run_id,
            branch_name=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=source_deleted,
            target_catalog_ref="main",
        ):
            promoted += 1
            logger.info(
                "wap_branch_promoted",
                run_id=run.run_id,
                branch_name=branch_name,
            )

    if promoted or blocked:
        logger.info(
            "wap_auto_promotion_sensor_completed",
            promoted=promoted,
            blocked=blocked,
            scanned_runs=len(terminal_runs),
        )

    context.update_cursor(evaluation_time.isoformat())


def _all_checks_passed(instance: Any, run_id: str) -> bool:
    """Return True when no ERROR-severity asset check failed in the run.

    Failed WARN-severity checks are non-blocking: recorded as durable
    warning evidence but never gating promotion (zero executed checks also
    counts as passed)."""

    try:
        check_records = instance.get_records_for_run(
            run_id,
            of_type=dg.DagsterEventType.ASSET_CHECK_EVALUATION,
        )
    except Exception:
        logger.warning(
            "wap_all_checks_passed_filter_failed",
            run_id=run_id,
            exc_info=True,
        )
        return False

    blocking_failure_seen = False
    warning_failures = 0
    for record in check_records.records:
        entry = record.event_log_entry
        check_eval = getattr(entry, "asset_check_evaluation", None)
        if check_eval is None:
            continue
        if check_eval.passed:
            continue
        if _severity_label(getattr(check_eval, "severity", None)) == "warn":
            warning_failures += 1
            continue
        blocking_failure_seen = True
    # Failed WARN-severity checks are recorded as evidence but never gate
    # promotion; only ERROR-severity failures block.
    if warning_failures:
        logger.info(
            "wap_promotion_warn_check_failures_non_blocking",
            warning_failures=warning_failures,
        )
    return not blocking_failure_seen


# ---------------------------------------------------------------------------
# Sensor 2: Branch cleanup
# ---------------------------------------------------------------------------


@dg.sensor(
    name="wap_branch_cleanup_sensor",
    description="Deletes stale WAP pipeline branches past retention period",
    minimum_interval_seconds=DEFAULT_CLEANUP_INTERVAL_SECONDS,
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def wap_branch_cleanup_sensor(context: dg.SensorEvaluationContext):
    """Delete stale WAP pipeline branches past the retention period.

    Scans the versioned catalog for branches matching the ``pipeline-run-``
    prefix.  For each branch past the 24-hour retention threshold the suffix is
    treated as a logical run ID: Dagster is queried by the ``phlo/run_id`` tag
    and only runs whose ``phlo/wap_branch`` tag exactly equals the candidate
    branch are considered.  The branch is deleted only when a single exact
    match exists, it is terminal, and its project/attempt correlation resolves.

    Branches are retained and an incomplete maintenance evidence gap is
    recorded when the matched run is active, absent, ambiguous, or conflicts on
    project/attempt metadata. Unacknowledged merge intents are also retained
    until their catalog outcome is resolved. The logical run ID is the cleanup/report
    identity; the physical Dagster run ID is used only for status.

    """
    catalog = _load_versioned_catalog()
    query_catalog_manager = _load_ref_query_catalog_manager()

    retention_cutoff = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_RETENTION_HOURS)

    branches = catalog.list_branches()
    pipeline_branches = [b for b in branches if _is_owned_wap_branch(b.name)]

    deleted = 0
    skipped = 0

    for branch in pipeline_branches:
        if not branch.created_at or branch.created_at > retention_cutoff:
            skipped += 1
            continue

        logical_run_id = branch.name.removeprefix(OWNED_WAP_BRANCH_PREFIX)

        # Correlate the candidate branch to its Dagster run through the logical
        # ``phlo/run_id`` tag, then require the exact ``phlo/wap_branch`` tag.
        # The branch suffix is a logical run ID, never a physical Dagster ID.
        candidate_runs = list(
            context.instance.get_runs(filters=dg.RunsFilter(tags={WAP_RUN_ID_TAG: logical_run_id}))
        )
        exact_matches = [
            run
            for run in candidate_runs
            if (getattr(run, "tags", {}) or {}).get(WAP_BRANCH_TAG) == branch.name
        ]

        # Absent: no run carries both the logical ID and the exact WAP branch.
        if not exact_matches:
            _record_uncorrelated_gap(
                logical_run_id,
                branch=branch.name,
                missing=["run_status"],
                reason="cleanup_no_exact_tagged_run",
            )
            continue

        # Ambiguous or conflicting: more than one exact match.  Retain the
        # branch whether the matches merely duplicate or actively disagree on
        # project/attempt correlation.
        if len(exact_matches) > 1:
            project_ids = {
                (getattr(run, "tags", {}) or {}).get("phlo/project_id") for run in exact_matches
            }
            attempts = {
                (getattr(run, "tags", {}) or {}).get("phlo/attempt") for run in exact_matches
            }
            if len(project_ids) > 1 or len(attempts) > 1:
                _record_uncorrelated_gap(
                    logical_run_id,
                    branch=branch.name,
                    missing=["project_id", "attempt"],
                    reason="cleanup_correlation_conflict",
                )
            else:
                _record_uncorrelated_gap(
                    logical_run_id,
                    branch=branch.name,
                    missing=["run_status"],
                    reason="cleanup_ambiguous_tagged_runs",
                )
            continue

        # Exactly one exact match: use its physical ID only for Dagster status.
        selected_run = exact_matches[0]
        run_status = _normalized_dagster_status(selected_run)

        # Active: the matched run has not reached a terminal state.
        if run_status is None:
            _record_uncorrelated_gap(
                logical_run_id,
                branch=branch.name,
                missing=["run_status"],
                reason="cleanup_run_active",
            )
            continue

        # Fail closed when the matched run lacks project/attempt correlation.
        cleanup_run = type(
            "CleanupRun",
            (),
            {
                "run_id": logical_run_id,
                "tags": dict(getattr(selected_run, "tags", {}) or {}),
            },
        )()
        project_id = _project_id_for_run(cleanup_run)
        attempt = _attempt_for_run(cleanup_run)
        if not project_id or attempt is None:
            _record_uncorrelated_gap(
                logical_run_id,
                branch=branch.name,
                missing=[
                    field
                    for field, value in (("project_id", project_id), ("attempt", attempt))
                    if not value
                ],
                reason="cleanup_report_missing_correlation",
            )
            continue

        report = _read_wap_report(logical_run_id)
        if report and report.get("merge_state") == "merge_started":
            # Retention cannot resolve a lost acknowledgement. This ref may
            # still be the only recoverable copy of an unpublished batch.
            skipped += 1
            logger.warning(
                "wap_cleanup_merge_recovery_required",
                run_id=logical_run_id,
                branch_name=branch.name,
            )
            continue

        cleanup_complete = _cleanup_owned_wap_branch(
            catalog,
            branch.name,
            query_catalog_manager,
        )
        write_wap_report(
            logical_run_id,
            status="cleanup_complete" if cleanup_complete else "cleanup_incomplete",
            branch=branch.name,
            cleanup_complete=cleanup_complete,
            failure_reason=None if cleanup_complete else "branch_cleanup_incomplete",
        )
        if cleanup_complete:
            deleted += 1
            logger.info(
                "wap_branch_cleaned_up",
                branch_name=branch.name,
                created_at=branch.created_at.isoformat() if branch.created_at else None,
            )
            _emit_wap_observation(
                run=cleanup_run,
                status="success",
                run_status=run_status,
                operation="cleanup",
                catalog_ref=branch.name,
                source_hash=branch.hash,
                merge_outcome="deleted",
                metadata={"retention_hours": DEFAULT_RETENTION_HOURS},
            )
        else:
            logger.warning(
                "wap_branch_cleanup_failed",
                branch_name=branch.name,
            )

    if deleted or skipped:
        logger.info(
            "wap_branch_cleanup_sensor_completed",
            deleted=deleted,
            skipped=skipped,
            total_pipeline_branches=len(pipeline_branches),
        )


# ---------------------------------------------------------------------------
# Definitions helper
# ---------------------------------------------------------------------------


def _iter_snapshot_strategy_reports() -> list[dict[str, Any]]:
    """List durable reports launched under the snapshot strategy."""
    try:
        report_dir = _report_path("probe").parent
        paths = sorted(report_dir.glob("*.json"))
    except OSError:
        return []
    reports: list[dict[str, Any]] = []
    for path in paths:
        if path.parent.name != "wap-reports":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("strategy") == WAP_STRATEGY_SNAPSHOT:
            reports.append(payload)
    return reports


@dg.sensor(
    name="wap_candidate_cleanup_sensor",
    description="Aborts stale snapshot-strategy candidate namespaces past retention",
    minimum_interval_seconds=DEFAULT_CLEANUP_INTERVAL_SECONDS,
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def wap_candidate_cleanup_sensor(context: dg.SensorEvaluationContext):
    """Abort stale snapshot-strategy candidate namespaces past retention.

    Correlates each snapshot-strategy report to exactly one terminal Dagster
    run through the logical ``phlo/run_id`` tag plus the exact ``phlo/wap_branch``
    tag, mirroring the branch cleanup sensor. Candidate namespaces of runs
    that are active, ambiguous, or uncorrelated are retained for audit.
    """
    catalog = _load_snapshot_promotion_catalog()
    retention_cutoff = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_RETENTION_HOURS)

    aborted = 0
    skipped = 0

    for report in _iter_snapshot_strategy_reports():
        namespace = report.get("branch")
        logical_run_id = report.get("run_id")
        if not namespace or not logical_run_id:
            continue
        updated_at = report.get("updated_at")
        try:
            report_time = (
                datetime.fromisoformat(updated_at) if isinstance(updated_at, str) else None
            )
        except ValueError:
            report_time = None
        if report_time is None or report_time > retention_cutoff:
            skipped += 1
            continue

        candidate_runs = list(
            context.instance.get_runs(
                filters=dg.RunsFilter(tags={WAP_RUN_ID_TAG: str(logical_run_id)})
            )
        )
        exact_matches = [
            run
            for run in candidate_runs
            if (getattr(run, "tags", {}) or {}).get(WAP_BRANCH_TAG) == namespace
        ]
        if len(exact_matches) != 1:
            _record_uncorrelated_gap(
                str(logical_run_id),
                branch=str(namespace),
                missing=["run_status"],
                reason="cleanup_ambiguous_or_missing_tagged_run",
            )
            continue

        selected_run = exact_matches[0]
        run_status = _normalized_dagster_status(selected_run)
        if run_status is None:
            skipped += 1
            continue

        cleanup_run = type(
            "CleanupRun",
            (),
            {
                "run_id": logical_run_id,
                "tags": dict(getattr(selected_run, "tags", {}) or {}),
            },
        )()
        cleanup_complete = _cleanup_owned_candidates(catalog, str(namespace))
        write_wap_report(
            str(logical_run_id),
            status="cleanup_complete" if cleanup_complete else "cleanup_incomplete",
            branch=namespace,
            cleanup_complete=cleanup_complete,
            failure_reason=None if cleanup_complete else "candidate_cleanup_incomplete",
        )
        if cleanup_complete:
            aborted += 1
            logger.info(
                "wap_candidates_cleaned_up",
                branch_name=namespace,
                dagster_run_id=selected_run.run_id,
            )
            _emit_wap_observation(
                run=cleanup_run,
                status="success",
                run_status=run_status,
                operation="cleanup",
                catalog_ref=str(namespace),
                source_hash=None,
                merge_outcome="deleted",
                metadata={"retention_hours": DEFAULT_RETENTION_HOURS},
            )
        else:
            logger.warning("wap_candidate_cleanup_failed", branch_name=namespace)

    if aborted or skipped:
        logger.info(
            "wap_candidate_cleanup_sensor_completed",
            aborted=aborted,
            skipped=skipped,
        )


def get_wap_definitions() -> dg.Definitions:
    """Return Dagster definitions for the WAP lifecycle sensors.

    Merge into your project definitions to enable automated Write-Audit-Publish.
    The cleanup sensor matches the configured strategy: branch retention for
    versioned catalogs, candidate-namespace retention for snapshot promotion.

    """
    from phlo.infrastructure import load_wap_config

    strategy = load_wap_config().strategy
    cleanup_sensor = (
        wap_candidate_cleanup_sensor
        if strategy == WAP_STRATEGY_SNAPSHOT
        else wap_branch_cleanup_sensor
    )
    logger.info(
        "dagster_wap_definitions_built",
        sensor_count=2,
        strategy=strategy,
    )
    return dg.Definitions(
        sensors=[
            wap_auto_promotion_sensor,
            cleanup_sensor,
        ],
    )
