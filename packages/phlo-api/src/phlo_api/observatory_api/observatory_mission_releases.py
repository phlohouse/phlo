"""Releases read models: promotion control, candidate review and history.

Candidates are addressed by candidate id. The summary band is derived from the
candidate and completed-release collections so the counters can never disagree
with the rows they describe.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from phlo_api.api.operation_controls import (
    audit_operation,
    enforce_rate_limit,
    replay_or_execute,
    require_scope,
)
from phlo_api.observatory_api.observatory_mission_control_mode import (
    data_mode,
    demo_envelope,
    live_envelope,
    outcome_data,
    reject_demo_mutations,
)
from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    CompletedRelease,
    PromotionPreview,
    PromotionRequest,
    PromotionResult,
    ReleaseCandidate,
    ReleaseCandidateDetail,
    SummaryMetricRow,
)
from phlo_api.observatory_api.observatory_mission_control_sources import (
    build_release_summary,
    derive_completed_releases,
    derive_promotion_preview,
    derive_release_candidate,
    derive_release_candidates,
    derive_release_summary,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    serve_collection,
    serve_sourced,
    serve_sourced_record,
)
from phlo_api.observatory_api.observatory_promotion import execute_manual_promotion
from phlo_api.observatory_api.run_action_contract import require_idempotency_key

router = APIRouter()


@router.get("/mission/releases/summary")
def get_mission_release_summary() -> ReadEnvelope[list[SummaryMetricRow]]:
    """Return the Releases summary band, derived from live catalog branches."""
    if data_mode() == "demo":
        return _stored_release_summary()

    candidates = outcome_data(derive_release_candidates())
    completed = outcome_data(derive_completed_releases())
    return live_envelope(derive_release_summary(candidates, completed), source="catalog")


def _stored_release_summary() -> ReadEnvelope[list[SummaryMetricRow]]:
    """Compute the summary band from the stored/seed collections (demo mode)."""
    candidates = serve_collection("release_candidates", ReleaseCandidate).data or []
    completed = serve_collection("completed_releases", CompletedRelease).data or []
    return demo_envelope(build_release_summary(candidates, completed))


@router.get("/mission/releases/candidates")
def get_mission_release_candidates() -> ReadEnvelope[list[ReleaseCandidate]]:
    """Return candidates awaiting promotion, derived from unmerged branches."""
    return serve_sourced(derive_release_candidates, "release_candidates", ReleaseCandidate)


# Registered before the catch-all: a ":path" converter would otherwise
# swallow the /preview suffix into the candidate id.
@router.get(
    "/mission/releases/candidates/{candidate_id:path}/preview",
    response_model=ReadEnvelope[PromotionPreview],
)
def get_mission_release_candidate_preview(
    candidate_id: str,
) -> ReadEnvelope[PromotionPreview]:
    """Read-only promotion preview: re-evaluates the WAP gates (owned staging
    ref, launch binding, strategy, audit decision, current revisions) and binds
    the inputs into a digest. Never mutates catalog state."""
    return serve_sourced_record(
        lambda: derive_promotion_preview(candidate_id),
        "promotion_preview",
        PromotionPreview,
        f"Unknown or non-promotable candidate {candidate_id}",
        id=candidate_id,
    )


# Branch names contain "/" (e.g. wap/retail_sales_daily), so the id must be
# captured as a path segment rather than a single segment.
@router.get("/mission/releases/candidates/{candidate_id:path}")
def get_mission_release_candidate(candidate_id: str) -> ReadEnvelope[ReleaseCandidateDetail]:
    """Return the full review payload for one candidate branch."""
    return serve_sourced_record(
        lambda: derive_release_candidate(candidate_id),
        "release_candidate_detail",
        ReleaseCandidateDetail,
        f"Unknown release candidate {candidate_id}",
        id=candidate_id,
    )


@router.post(
    "/mission/releases/candidates/{candidate_id:path}/promotion",
    response_model=PromotionResult,
    response_model_exclude_none=True,
)
def post_mission_release_promotion(
    candidate_id: str,
    request: PromotionRequest,
    http_request: Request,
) -> PromotionResult:
    """Promote one audited candidate through the shared WAP authority.

    The guard chain (demo rejection, ``lakehouse:operate``, rate limit,
    mandatory idempotency key, payload-digest binding) runs before any
    catalog mutation. ``preview_digest`` binds the confirmation to the exact
    preview the operator reviewed; inside the per-candidate promotion lock
    the gates and revisions are re-evaluated, so a competing sensor win
    reconciles as already-promoted and moved evidence refuses rather than
    executing a second promotion.
    """
    reject_demo_mutations()
    auth = require_scope(http_request, "lakehouse:operate")
    enforce_rate_limit(auth["subject"], "promote_release_candidate")
    require_idempotency_key(request.idempotency_key)

    def execute() -> dict[str, object]:
        result = execute_manual_promotion(
            candidate_id, expected_preview_digest=request.preview_digest
        )
        # The durable report changed; drop cached read models so the next
        # GET reflects the promotion instead of a pre-commit snapshot.
        from phlo_api.observatory_api import observatory as _observatory

        _observatory._clear_read_model_cache()
        return {
            "outcome": result.outcome,
            "candidate_id": result.candidate_id,
            "blockers": result.blockers,
            "preview_digest": result.preview_digest,
            "source_revision": result.source_revision,
            "target_revision_before": result.target_revision_before,
            "target_revision_after": result.target_revision_after,
            "staging_ref": result.staging_ref,
            "source_deleted": result.source_deleted,
            "resumed": result.resumed,
            "failure_reason": result.failure_reason,
            "failure_detail": result.failure_detail,
            "release_revision": result.release_revision,
        }

    request_payload = request.model_dump(mode="json")
    payload = replay_or_execute(
        idempotency_key=request.idempotency_key,
        operation="promote_release_candidate",
        target=candidate_id,
        execute=execute,
        payload=request_payload,
        audit=lambda result: audit_operation(
            operation="promote_release_candidate",
            target=candidate_id,
            dry_run=False,
            auth=auth,
            payload=request_payload,
            result=result,
        ),
        audit_intent=lambda: audit_operation(
            operation="promote_release_candidate",
            target=candidate_id,
            dry_run=False,
            auth=auth,
            payload={**request_payload, "phase": "intent"},
        ),
    )
    return PromotionResult.model_validate(payload)


@router.get("/mission/releases/completed")
def get_mission_completed_releases() -> ReadEnvelope[list[CompletedRelease]]:
    """Return releases confirmed by promoted runs."""
    return serve_sourced(derive_completed_releases, "completed_releases", CompletedRelease)
