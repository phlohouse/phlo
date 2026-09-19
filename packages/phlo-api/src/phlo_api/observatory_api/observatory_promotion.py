"""Operator-triggered WAP promotion through the shared authority.

Runs the same durable state machine the auto-promotion sensor runs —
``phlo.wap_promotion.promote_wap_candidate`` — under the same per-candidate
lock, with the preview gates re-evaluated inside that lock so the commit
cannot act on stale revisions. The durable lifecycle report is the shared
claim and receipt: a sensor win reconciles as already-promoted here, and an
interrupted command resumes from ``merge_state`` on the next call rather
than re-executing a merge that may have landed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from phlo.capabilities.interfaces import RefQueryCatalogManager, SnapshotPromotionCatalog
from phlo.capabilities.resolver import resolve_capability
from phlo.logging import get_logger
from phlo.wap_promotion import (
    WAP_STRATEGY_SNAPSHOT,
    PromotionAdvance,
    promote_wap_candidate,
)
from phlo.wap_reports import (
    WAP_TARGET_BRANCH,
    promotion_lock,
    read_wap_report,
    write_wap_report,
)

from phlo_api.observatory_api.observatory_mission_control_state import project_root
from phlo_api.observatory_api.observatory_wap import (
    WAP_RELEASED_STATUSES,
    explain_failure,
    load_wap_reports,
)

logger = get_logger(__name__)

PromotionOutcome = Literal[
    "promoted",
    "already_promoted",
    "blocked",
    "stale_preview",
    "conflict",
    "merge_failed",
    "outbox_unavailable",
    "cleanup_pending",
    "reconcile_pending",
    "recovery_required",
    "verification_pending",
    "capability_unavailable",
    "unknown_candidate",
]


@dataclass(frozen=True, slots=True)
class PromotionCommandResult:
    """The truthful outcome of one promotion command — never a guess."""

    outcome: PromotionOutcome
    candidate_id: str
    blockers: list[str] = field(default_factory=list)
    preview_digest: str | None = None
    source_revision: str | None = None
    target_revision_before: str | None = None
    target_revision_after: str | None = None
    staging_ref: str | None = None
    source_deleted: bool = False
    resumed: bool = False
    failure_reason: str | None = None
    failure_detail: str | None = None
    release_revision: str | None = None


def _result_from_report(
    outcome: PromotionOutcome, candidate_id: str, report: dict[str, Any], **extra: Any
) -> PromotionCommandResult:
    return PromotionCommandResult(
        outcome=outcome,
        candidate_id=candidate_id,
        staging_ref=report.get("branch"),
        source_revision=report.get("source_hash") or report.get("launch_source_hash"),
        target_revision_before=report.get("target_hash_before"),
        target_revision_after=report.get("target_hash_after"),
        source_deleted=bool(report.get("source_deleted")),
        failure_reason=report.get("failure_reason"),
        failure_detail=report.get("failure_detail"),
        release_revision=report.get("target_hash_after") or report.get("release_id"),
        **extra,
    )


def _query_catalog_manager() -> RefQueryCatalogManager | None:
    """Resolve the optional per-ref query-catalog cleanup capability."""
    try:
        resolution = resolve_capability("query_engine")
    except Exception:
        return None
    if resolution is None or not isinstance(resolution.provider, RefQueryCatalogManager):
        return None
    return resolution.provider


def _release_visible(catalog: Any, advance: PromotionAdvance, strategy: str) -> bool:
    """Confirm the commit is what consumers resolve before writing the receipt.

    Branch strategy: the target ref's head is the revision the receipt
    recorded. Snapshot strategy: every audited candidate snapshot resolves to
    this release through the provider's release pointer.
    """
    try:
        if strategy == WAP_STRATEGY_SNAPSHOT:
            for row in advance.candidate_rows:
                record = catalog.resolve_release(table_name=row["table"])
                if record is None or record.release_id != advance.logical_run_id:
                    return False
            return bool(advance.candidate_rows)
        current = catalog.get_branch_hash(WAP_TARGET_BRANCH)
    except Exception:
        logger.warning("promotion_post_commit_verify_failed", exc_info=True)
        return False
    return current is not None and current == advance.target_hash_after


def execute_manual_promotion(
    candidate_id: str, *, expected_preview_digest: str | None
) -> PromotionCommandResult:
    """Run the guarded promotion command for one release candidate.

    Preview gates are evaluated twice: once before the lock so an operator
    gets blockers without mutating, and once inside it so the commit cannot
    proceed on revisions that moved between preview and dispatch.
    """
    # Deferred: sources imports nothing from this module, and keeping the
    # catalog/report helpers in one place avoids a second resolver.
    from phlo_api.observatory_api import observatory_mission_control_sources as sources

    root = project_root()
    reports = load_wap_reports(root)
    report = sources._wap_report_for(candidate_id, reports)
    if report is None:
        return PromotionCommandResult(
            outcome="unknown_candidate",
            candidate_id=candidate_id,
            blockers=[f"Unknown release candidate {candidate_id}"],
        )
    if report.status in WAP_RELEASED_STATUSES:
        # A competing promotion already finalized — reconcile, never re-run.
        return _result_from_report(
            "already_promoted", candidate_id, _report_payload(report), resumed=True
        )

    # A report already carrying merge_state is mid-sequence: the catalog
    # mutation may already be committed, so fresh-promotion gates (which
    # compare the launch-time target to the current one) do not apply.
    # Resume is governed by merge_state inside the shared sequence instead.
    inflight = str(report.raw.get("merge_state") or "") in {"merge_started", "merged"}

    preview = None
    if not inflight:
        preview_outcome = sources.derive_promotion_preview(candidate_id)
        preview = preview_outcome.data
        if preview is None or not preview.eligible:
            blockers = (
                [check.detail for check in preview.checks if check.passed is not True]
                if preview is not None
                else ["Promotion preview unavailable"]
            )
            return PromotionCommandResult(
                outcome="blocked",
                candidate_id=candidate_id,
                blockers=blockers,
                preview_digest=preview.digest if preview else None,
            )
        if expected_preview_digest and preview.digest != expected_preview_digest:
            return PromotionCommandResult(
                outcome="stale_preview",
                candidate_id=candidate_id,
                blockers=[
                    "Candidate evidence changed since the preview was issued; re-preview before confirming"
                ],
                preview_digest=preview.digest,
            )

    catalog = sources._catalog_provider()
    strategy = report.strategy or "branch"
    preview_digest = preview.digest if preview else None
    if catalog is None:
        return PromotionCommandResult(
            outcome="capability_unavailable",
            candidate_id=candidate_id,
            blockers=["No catalog provider is configured for this environment"],
            preview_digest=preview_digest,
        )
    if strategy == WAP_STRATEGY_SNAPSHOT and not isinstance(catalog, SnapshotPromotionCatalog):
        return PromotionCommandResult(
            outcome="capability_unavailable",
            candidate_id=candidate_id,
            blockers=[
                "Report strategy is snapshot promotion but the configured catalog does not implement it"
            ],
            preview_digest=preview_digest,
        )
    if strategy != WAP_STRATEGY_SNAPSHOT and not callable(getattr(catalog, "merge_branch", None)):
        return PromotionCommandResult(
            outcome="capability_unavailable",
            candidate_id=candidate_id,
            blockers=[
                "Report strategy is branch merge but the configured catalog cannot merge branches"
            ],
            preview_digest=preview_digest,
        )

    with promotion_lock(root, report.logical_run_id):
        # Commit-time recheck: the sensor may have won, or revisions may have
        # moved, while this request waited on the lock.
        fresh_report = read_wap_report(root, report.logical_run_id)
        if fresh_report and fresh_report.get("status") in WAP_RELEASED_STATUSES:
            return _result_from_report("already_promoted", candidate_id, fresh_report, resumed=True)
        fresh_inflight = str((fresh_report or {}).get("merge_state") or "") in {
            "merge_started",
            "merged",
        }
        if not fresh_inflight:
            fresh_preview = sources.derive_promotion_preview(candidate_id)
            fresh = fresh_preview.data
            if (
                fresh is None
                or not fresh.eligible
                or (expected_preview_digest and fresh.digest != expected_preview_digest)
            ):
                return PromotionCommandResult(
                    outcome="stale_preview" if fresh is not None else "blocked",
                    candidate_id=candidate_id,
                    blockers=[
                        "Candidate evidence changed while the promotion was queued; re-preview before confirming"
                    ],
                    preview_digest=fresh.digest if fresh else None,
                )
        advance = promote_wap_candidate(
            logical_run_id=report.logical_run_id,
            branch_name=report.branch,
            strategy=strategy,
            catalog=catalog,
            query_catalog_manager=_query_catalog_manager(),
            report_reader=lambda rid: read_wap_report(root, rid),
            report_writer=lambda rid, **updates: write_wap_report(root, rid, **updates),
        )

        # Verification and the terminal receipt stay inside the lock: a
        # competing confirmation entering after the merge but before the
        # receipt would otherwise stamp its own finalize over ours.
        if advance.state != "advanced":
            outcomes: dict[str, PromotionOutcome] = {
                "conflict": "conflict",
                "merge_failed": "merge_failed",
                "outbox_failed": "outbox_unavailable",
                "receipt_failed": "outbox_unavailable",
                "cleanup_pending": "cleanup_pending",
                "reconcile_pending": "reconcile_pending",
                "recovery_required": "recovery_required",
            }
            return PromotionCommandResult(
                outcome=outcomes[advance.state],
                candidate_id=candidate_id,
                preview_digest=preview_digest,
                staging_ref=advance.branch,
                source_revision=advance.source_hash,
                target_revision_before=advance.target_hash_before,
                target_revision_after=advance.target_hash_after,
                source_deleted=advance.source_deleted,
                failure_reason=advance.failure_reason,
                failure_detail=advance.failure_detail,
                blockers=[explain_failure(advance.failure_reason)]
                if advance.failure_reason
                else [],
            )

        if not _release_visible(catalog, advance, strategy):
            # Provider committed but consumer-visible state did not verify;
            # leave the receipt unfinished so the next call re-checks rather
            # than stamping a release nobody can read.
            return PromotionCommandResult(
                outcome="verification_pending",
                candidate_id=candidate_id,
                preview_digest=preview_digest,
                staging_ref=advance.branch,
                source_revision=advance.source_hash,
                target_revision_before=advance.target_hash_before,
                target_revision_after=advance.target_hash_after,
                source_deleted=advance.source_deleted,
                failure_reason="post-commit release state did not verify",
            )

        finalized = write_wap_report(
            root,
            report.logical_run_id,
            status="promoted",
            merge_state="merged",
            branch=advance.branch,
            source_hash=advance.source_hash,
            target_branch=WAP_TARGET_BRANCH,
            target_hash_before=advance.target_hash_before,
            target_hash_after=advance.target_hash_after,
            source_deleted=advance.source_deleted,
            release_id=report.logical_run_id,
            promotion_origin="operator",
        )
        if not finalized:
            return PromotionCommandResult(
                outcome="outbox_unavailable",
                candidate_id=candidate_id,
                preview_digest=preview_digest,
                staging_ref=advance.branch,
                source_revision=advance.source_hash,
                target_revision_before=advance.target_hash_before,
                target_revision_after=advance.target_hash_after,
                source_deleted=advance.source_deleted,
                failure_reason="terminal promotion receipt could not be persisted",
            )
    return PromotionCommandResult(
        outcome="promoted",
        candidate_id=candidate_id,
        preview_digest=preview_digest,
        staging_ref=advance.branch,
        source_revision=advance.source_hash,
        target_revision_before=advance.target_hash_before,
        target_revision_after=advance.target_hash_after,
        source_deleted=advance.source_deleted,
        resumed=advance.resumed,
        release_revision=advance.target_hash_after,
    )


def _report_payload(report: Any) -> dict[str, Any]:
    raw = getattr(report, "raw", None)
    return dict(raw) if isinstance(raw, dict) else {}
