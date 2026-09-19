"""Governance read models: publication reviews, access drift, ownership gaps,
and the audit trail.

The access-drift surface is deliberately three-runged (declared -> compiled ->
verified). Drift is the statement that the verified backend grants differ from
the compiled grants, which is why the evidence rows carry an explicit flag
rather than a free-text verdict alone.
"""

from __future__ import annotations

from fastapi import APIRouter

from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    AccessDrift,
    AuditEvent,
    OwnershipGap,
    PublicationPlan,
    PublicationReview,
    SummaryMetricRow,
)
from phlo_api.observatory_api.observatory_mission_control_sources import (
    derive_ownership_gaps,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    serve_collection,
    serve_record,
    serve_sourced,
)

router = APIRouter()


@router.get("/mission/governance/publication-reviews")
def get_mission_publication_reviews() -> ReadEnvelope[list[PublicationReview]]:
    """Return datasets awaiting a publication policy verdict."""
    return serve_collection("publication_reviews", PublicationReview)


@router.get("/mission/governance/access-drift")
def get_mission_access_drift() -> ReadEnvelope[list[AccessDrift]]:
    """Return detected divergence between declared and enforced access."""
    return serve_collection("access_drift", AccessDrift)


@router.get("/mission/governance/ownership-gaps")
def get_mission_ownership_gaps() -> ReadEnvelope[list[OwnershipGap]]:
    """Return datasets missing an ownership or contract requirement.

    Live mode derives gaps from declared asset metadata; an empty list means
    every observable asset declares what it needs, and an unreachable provider
    is a 503 rather than a silently empty report.
    """
    return serve_sourced(derive_ownership_gaps, "ownership_gaps", OwnershipGap)


@router.get("/mission/governance/audit")
def get_mission_audit_events() -> ReadEnvelope[list[AuditEvent]]:
    """Return the immutable governance action log, newest first."""
    return serve_collection("audit_events", AuditEvent)


@router.get("/mission/governance/summary")
def get_mission_governance_summary() -> ReadEnvelope[list[SummaryMetricRow]]:
    """Return the Governance summary band."""
    return serve_collection("governance_summary", SummaryMetricRow)


@router.get("/mission/governance/publication-plan/{dataset_id:path}")
def get_mission_publication_plan(dataset_id: str) -> ReadEnvelope[PublicationPlan]:
    """Return the resolved publication plan for a dataset."""
    return serve_record(
        "publication_plan",
        PublicationPlan,
        f"No publication plan for {dataset_id}",
        dataset_id=dataset_id,
    )
