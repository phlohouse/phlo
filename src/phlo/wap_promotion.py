"""Governed WAP promotion sequence shared by all promotion callers.

The auto-promotion sensor and operator-triggered promotion commands run the
same durable state machine here: outbox intent → catalog mutation → merge
receipt → staging cleanup → caller finalization. Because the durable report
is the claim and the receipt, a caller that crashes mid-sequence — or races
the other caller — reconciles from ``merge_state`` instead of re-executing a
merge that may already have landed.

Both WAP strategies are implemented:

- ``branch``: ``VersionedCatalog.merge_branch`` guarded by the outbox.
- ``snapshot``: ``SnapshotPromotionCatalog.promote_candidates`` guarded by a
  compare-and-swap on the release pointer, with post-commit resolution
  checks so an interrupted attempt never guesses whether it published.

The function is deliberately orchestrator-free: callers supply the report
reader/writer (so test seams keep working), an optional ``emit`` hook for
observability, and finalization is left to the caller so the sensor can add
run tags and Dagster reconciliation while a manual command writes its own
terminal evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from phlo.capabilities.interfaces import RefQueryCatalogManager
from phlo.logging import get_logger

WAP_STRATEGY_BRANCH = "branch"
WAP_STRATEGY_SNAPSHOT = "snapshot"
OWNED_WAP_REF_PREFIX = "pipeline-run-"
WAP_TARGET_BRANCH = "main"

logger = get_logger(__name__)

PromotionState = Literal[
    "advanced",
    "conflict",
    "merge_failed",
    "outbox_failed",
    "receipt_failed",
    "cleanup_pending",
    "reconcile_pending",
    "recovery_required",
]

ReportReader = Callable[[str], "dict[str, Any] | None"]
ReportWriter = Callable[..., bool]
EmitHook = Callable[..., None]
ReconcileHook = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class PromotionAdvance:
    """The outcome of one pass through the durable promotion sequence.

    ``advanced`` means the catalog mutation, merge receipt and cleanup
    checkpoint are all durable — the caller should write its terminal
    evidence. Every other state is a resumable or refused outcome the caller
    must surface truthfully instead of guessing.
    """

    state: PromotionState
    logical_run_id: str
    branch: str | None
    source_hash: str | None = None
    target_hash_before: str | None = None
    target_hash_after: str | None = None
    source_deleted: bool = False
    resumed: bool = False
    failure_reason: str | None = None
    failure_detail: str | None = None
    candidate_rows: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def _branch_hash(catalog: Any, branch: str) -> str | None:
    try:
        return catalog.get_branch_hash(branch)
    except Exception:
        logger.warning("wap_branch_hash_read_failed", branch=branch, exc_info=True)
        return None


def _release_revision(catalog: Any) -> int:
    try:
        return int(catalog.release_revision())
    except Exception:
        logger.warning("wap_release_revision_read_failed", exc_info=True)
        return -1


def _is_owned_wap_ref(ref: str | None) -> bool:
    return bool(ref and ref.startswith(OWNED_WAP_REF_PREFIX))


def cleanup_owned_wap_branch(
    catalog: Any,
    branch_name: str,
    query_catalog_manager: RefQueryCatalogManager | None,
) -> bool:
    """Clean up one owned WAP ref and its optional query catalog.

    The query catalog is removed first so a manager failure leaves the
    versioned branch available for a truthful retry. If branch deletion then
    fails, the operation is still incomplete; providers must make their
    catalog removal idempotent for the retry path.
    """
    if not _is_owned_wap_ref(branch_name):
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
        return bool(catalog.delete_branch(branch_name))
    except Exception:
        logger.warning("wap_branch_cleanup_failed", branch_name=branch_name, exc_info=True)
        return False


def _cleanup_owned_candidates(catalog: Any, namespace: str) -> bool:
    """Abort every candidate snapshot a run scoped under ``namespace``."""
    try:
        return bool(catalog.abort_candidates(namespace=namespace))
    except Exception:
        logger.warning("wap_candidate_cleanup_failed", namespace=namespace, exc_info=True)
        return False


def _release_resolved_for_run(catalog: Any, namespace: str, release_id: str) -> bool:
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


def promote_wap_candidate(
    *,
    logical_run_id: str,
    branch_name: str | None,
    strategy: str,
    catalog: Any,
    query_catalog_manager: RefQueryCatalogManager | None = None,
    emit: EmitHook | None = None,
    report_reader: ReportReader,
    report_writer: ReportWriter,
    reconcile: ReconcileHook | None = None,
) -> PromotionAdvance:
    """Advance one audited candidate through the durable promotion sequence.

    Reads the prior lifecycle report and resumes/refuses from its recorded
    ``merge_state`` — never re-executes a merge that may already have landed.
    Returns ``advanced`` once provider commit, receipt and cleanup are all
    durable; the caller then writes its terminal evidence.
    """
    if not _is_owned_wap_ref(branch_name):
        logger.warning("wap_promotion_rejected_unowned_ref", branch_name=branch_name)
        return PromotionAdvance(
            state="conflict",
            logical_run_id=logical_run_id,
            branch=branch_name,
            failure_reason="staging ref is not an owned WAP ref",
        )
    prior = report_reader(logical_run_id)
    if strategy == WAP_STRATEGY_SNAPSHOT:
        return _advance_snapshot(
            catalog=catalog,
            branch_name=branch_name or "",
            logical_run_id=logical_run_id,
            prior=prior,
            emit=emit,
            report_writer=report_writer,
        )
    return _advance_branch(
        catalog=catalog,
        branch_name=branch_name or "",
        logical_run_id=logical_run_id,
        prior=prior,
        query_catalog_manager=query_catalog_manager,
        emit=emit,
        report_writer=report_writer,
        reconcile=reconcile,
    )


def _emit(
    emit: EmitHook | None,
    *,
    operation: str,
    status: str,
    merge_outcome: str,
    catalog_ref: str,
    source_hash: str | None = None,
    target_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
    **extra: Any,
) -> None:
    if emit is None:
        return
    try:
        emit(
            operation=operation,
            status=status,
            merge_outcome=merge_outcome,
            catalog_ref=catalog_ref,
            source_hash=source_hash,
            target_hash=target_hash,
            metadata=metadata,
            **extra,
        )
    except Exception:
        logger.warning("wap_promotion_emit_failed", exc_info=True)


def _advance_branch(
    *,
    catalog: Any,
    branch_name: str,
    logical_run_id: str,
    prior: dict[str, Any] | None,
    query_catalog_manager: RefQueryCatalogManager | None,
    emit: EmitHook | None,
    report_writer: ReportWriter,
    reconcile: ReconcileHook | None,
) -> PromotionAdvance:
    source_hash = _branch_hash(catalog, branch_name)
    target_hash_before = _branch_hash(catalog, WAP_TARGET_BRANCH)
    already_merged = prior is not None and prior.get("merge_state") == "merged"
    merge_started = prior is not None and prior.get("merge_state") == "merge_started"

    if already_merged and prior is not None:
        source_hash = prior.get("source_hash") or source_hash
        target_hash_before = prior.get("target_hash_before") or target_hash_before
        merged = True
    elif merge_started:
        # Intent alone cannot distinguish a rejected merge from a committed
        # merge whose acknowledgement was lost. Target movement may belong
        # to another writer; retain the source until a receipt is available.
        report_writer(
            logical_run_id,
            status="promotion_pending",
            failure_reason="merge_outcome_unknown",
        )
        logger.warning(
            "wap_promotion_merge_recovery_required",
            branch_name=branch_name,
        )
        return PromotionAdvance(
            state="recovery_required",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            failure_reason="merge_outcome_unknown",
        )
    else:
        # Persist intent before crossing the catalog boundary.  This is a
        # retry record, not a terminal promotion marker.
        if not report_writer(
            logical_run_id,
            status="promotion_pending",
            merge_state="merge_started",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=target_hash_before,
        ):
            logger.warning("wap_promotion_outbox_write_failed", logical_run_id=logical_run_id)
            return PromotionAdvance(
                state="outbox_failed",
                logical_run_id=logical_run_id,
                branch=branch_name,
                source_hash=source_hash,
                target_hash_before=target_hash_before,
            )
        # Prefer providers that report why a merge was refused; the bool-only
        # contract leaves the cause opaque to operators.
        merge_detail = getattr(catalog, "merge_branch_detail", None)
        if callable(merge_detail):
            merged, merge_error = merge_detail(source=branch_name, target=WAP_TARGET_BRANCH)
        else:
            merged = bool(catalog.merge_branch(source=branch_name, target=WAP_TARGET_BRANCH))
            merge_error = None

    if not merged:
        report_writer(
            logical_run_id,
            status="promotion_failed",
            merge_state="merge_failed",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=target_hash_before,
            failure_reason="merge_branch_returned_false",
            failure_detail=merge_error,
        )
        _emit(
            emit,
            operation="promotion",
            status="failed",
            merge_outcome="failed",
            catalog_ref=WAP_TARGET_BRANCH,
            source_hash=source_hash,
            target_hash=target_hash_before,
            metadata={"changed_content_keys": {"status": "unavailable"}},
            refresh_quality=True,
        )
        logger.error(
            "wap_promotion_merge_failed",
            branch_name=branch_name,
        )
        return PromotionAdvance(
            state="merge_failed",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            failure_reason="merge_branch_returned_false",
            failure_detail=merge_error,
        )

    target_hash_after = _branch_hash(catalog, WAP_TARGET_BRANCH)
    # This acknowledged transition is what makes a subsequent evaluation
    # replay evidence/cleanup rather than invoke merge again.
    if not already_merged and not report_writer(
        logical_run_id,
        status="promotion_pending",
        merge_state="merged",
        branch=branch_name,
        source_hash=source_hash,
        target_branch=WAP_TARGET_BRANCH,
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
    ):
        logger.warning("wap_promotion_merge_receipt_write_failed", logical_run_id=logical_run_id)
        return PromotionAdvance(
            state="receipt_failed",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
        )

    source_deleted = bool(prior and prior.get("source_deleted"))
    if already_merged and not source_deleted:
        # A deleted ref is an idempotent cleanup success.  In particular,
        # do not turn a crash after cleanup into an endless retry because
        # providers correctly reject deletion of an absent branch.
        source_deleted = _branch_hash(catalog, branch_name) is None
    if not source_deleted:
        source_deleted = cleanup_owned_wap_branch(
            catalog,
            branch_name,
            query_catalog_manager,
        )
    if not source_deleted:
        report_writer(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=False,
        )
        logger.warning("wap_promotion_cleanup_pending", branch_name=branch_name)
        return PromotionAdvance(
            state="cleanup_pending",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
        )
    # Checkpoint cleanup independently of the terminal report.  A retry
    # after reconciliation or tag failure must not try to delete it again.
    if not report_writer(
        logical_run_id,
        status="promotion_pending",
        merge_state="merged",
        branch=branch_name,
        source_hash=source_hash,
        target_branch=WAP_TARGET_BRANCH,
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        source_deleted=True,
    ):
        logger.warning("wap_promotion_cleanup_receipt_write_failed", logical_run_id=logical_run_id)
        return PromotionAdvance(
            state="receipt_failed",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=True,
        )
    if reconcile is not None and not reconcile():
        logger.warning("wap_promotion_reconciliation_pending", branch_name=branch_name)
        return PromotionAdvance(
            state="reconcile_pending",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=True,
            failure_reason="reconciliation_pending",
        )
    return PromotionAdvance(
        state="advanced",
        logical_run_id=logical_run_id,
        branch=branch_name,
        source_hash=source_hash,
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        source_deleted=True,
        resumed=already_merged,
    )


def _advance_snapshot(
    *,
    catalog: Any,
    branch_name: str,
    logical_run_id: str,
    prior: dict[str, Any] | None,
    emit: EmitHook | None,
    report_writer: ReportWriter,
) -> PromotionAdvance:
    """Advance one snapshot-strategy run through promote → candidate abort."""
    already_merged = prior is not None and prior.get("merge_state") == "merged"
    merge_started = prior is not None and prior.get("merge_state") == "merge_started"
    current_revision = _release_revision(catalog)
    target_hash_before = str(current_revision) if current_revision >= 0 else None
    try:
        candidates = catalog.list_candidates(namespace=branch_name)
    except Exception:
        logger.warning(
            "wap_candidate_listing_failed",
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
        and prior is not None
        and prior.get("target_hash_before") != target_hash_before
        and _release_resolved_for_run(catalog, branch_name, logical_run_id)
    )
    if (
        merge_started
        and not resumed
        and prior is not None
        and prior.get("target_hash_before") != target_hash_before
    ):
        # The release pointer moved after our durable intent and our release
        # did not resolve: someone else published. Refuse to guess.
        report_writer(
            logical_run_id,
            status="promotion_failed",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=prior.get("target_hash_before"),
            failure_reason="release_pointer_conflict",
        )
        _emit(
            emit,
            operation="promotion",
            status="failed",
            merge_outcome="failed",
            catalog_ref=WAP_TARGET_BRANCH,
            source_hash=source_hash,
            target_hash=prior.get("target_hash_before"),
            metadata={"changed_content_keys": {"status": "unavailable"}},
        )
        logger.error(
            "wap_promotion_release_conflict",
            branch_name=branch_name,
        )
        return PromotionAdvance(
            state="conflict",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=prior.get("target_hash_before"),
            failure_reason="release_pointer_conflict",
            candidate_rows=tuple(candidate_rows),
        )

    if not resumed:
        launch_revision = (prior or {}).get("launch_target_hash_before")
        try:
            expected_revision = int(launch_revision) if launch_revision is not None else -1
        except (TypeError, ValueError):
            expected_revision = -1
        if expected_revision < 0 or expected_revision != current_revision:
            report_writer(
                logical_run_id,
                status="promotion_failed",
                branch=branch_name,
                failure_reason="release_pointer_conflict",
            )
            return PromotionAdvance(
                state="conflict",
                logical_run_id=logical_run_id,
                branch=branch_name,
                source_hash=source_hash,
                target_hash_before=target_hash_before,
                failure_reason="release_pointer_conflict",
                candidate_rows=tuple(candidate_rows),
            )
        if not report_writer(
            logical_run_id,
            status="promotion_pending",
            merge_state="merge_started",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=target_hash_before,
            candidates=candidate_rows,
        ):
            logger.warning("wap_promotion_outbox_write_failed", logical_run_id=logical_run_id)
            return PromotionAdvance(
                state="outbox_failed",
                logical_run_id=logical_run_id,
                branch=branch_name,
                source_hash=source_hash,
                target_hash_before=target_hash_before,
                candidate_rows=tuple(candidate_rows),
            )
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
                branch_name=branch_name,
                exc_info=True,
            )
            merged = False
        if not merged:
            report_writer(
                logical_run_id,
                status="promotion_failed",
                branch=branch_name,
                source_hash=source_hash,
                target_branch=WAP_TARGET_BRANCH,
                target_hash_before=target_hash_before,
                failure_reason="release_promotion_failed",
            )
            _emit(
                emit,
                operation="promotion",
                status="failed",
                merge_outcome="failed",
                catalog_ref=WAP_TARGET_BRANCH,
                source_hash=source_hash,
                target_hash=target_hash_before,
                metadata={"changed_content_keys": {"status": "unavailable"}},
            )
            logger.error(
                "wap_promotion_merge_failed",
                branch_name=branch_name,
            )
            return PromotionAdvance(
                state="merge_failed",
                logical_run_id=logical_run_id,
                branch=branch_name,
                source_hash=source_hash,
                target_hash_before=target_hash_before,
                failure_reason="release_promotion_failed",
                candidate_rows=tuple(candidate_rows),
            )
        target_hash_after = str(_release_revision(catalog))
        # This acknowledged transition is what makes a subsequent sensor
        # evaluation replay evidence/cleanup rather than promote again.
        if not report_writer(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            release_id=logical_run_id,
            candidates=candidate_rows,
        ):
            logger.warning(
                "wap_promotion_merge_receipt_write_failed", logical_run_id=logical_run_id
            )
            return PromotionAdvance(
                state="receipt_failed",
                logical_run_id=logical_run_id,
                branch=branch_name,
                source_hash=source_hash,
                target_hash_before=target_hash_before,
                target_hash_after=target_hash_after,
                candidate_rows=tuple(candidate_rows),
            )
    else:
        target_hash_after = str(_release_revision(catalog))

    source_deleted = bool(prior and prior.get("source_deleted"))
    if not source_deleted:
        source_deleted = _cleanup_owned_candidates(catalog, branch_name)
    if not source_deleted:
        report_writer(
            logical_run_id,
            status="promotion_pending",
            merge_state="merged",
            branch=branch_name,
            source_hash=source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=False,
        )
        logger.warning("wap_promotion_cleanup_pending", branch_name=branch_name)
        return PromotionAdvance(
            state="cleanup_pending",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            candidate_rows=tuple(candidate_rows),
        )
    # Checkpoint cleanup independently of the terminal report.  A retry
    # after reconciliation or tag failure must not try to abort it again.
    if not report_writer(
        logical_run_id,
        status="promotion_pending",
        merge_state="merged",
        branch=branch_name,
        source_hash=source_hash,
        target_branch=WAP_TARGET_BRANCH,
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        source_deleted=True,
    ):
        logger.warning("wap_promotion_cleanup_receipt_write_failed", logical_run_id=logical_run_id)
        return PromotionAdvance(
            state="receipt_failed",
            logical_run_id=logical_run_id,
            branch=branch_name,
            source_hash=source_hash,
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_after,
            source_deleted=True,
            candidate_rows=tuple(candidate_rows),
        )
    return PromotionAdvance(
        state="advanced",
        logical_run_id=logical_run_id,
        branch=branch_name,
        source_hash=source_hash,
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        source_deleted=True,
        resumed=bool(resumed),
        candidate_rows=tuple(candidate_rows),
    )
