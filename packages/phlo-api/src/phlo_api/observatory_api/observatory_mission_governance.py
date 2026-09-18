"""Governance read models: publication reviews, access drift, ownership gaps,
and the audit trail.

The access-drift surface is deliberately three-runged (declared -> compiled ->
verified). Drift is the statement that the verified backend grants differ from
the compiled grants, which is why the evidence rows carry an explicit flag
rather than a free-text verdict alone.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
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
    find_by,
    load_records,
)

router = APIRouter()


@router.get("/mission/governance/publication-reviews")
def get_mission_publication_reviews() -> list[PublicationReview]:
    """Return datasets awaiting a publication policy verdict."""
    return load_records("publication_reviews", PublicationReview)


@router.get("/mission/governance/access-drift")
def get_mission_access_drift() -> list[AccessDrift]:
    """Return detected divergence between declared and enforced access."""
    return load_records("access_drift", AccessDrift)


@router.get("/mission/governance/ownership-gaps")
def get_mission_ownership_gaps() -> list[OwnershipGap]:
    """Return datasets missing an ownership or contract requirement.

    Declared asset metadata answers this directly, so it is preferred over the
    seeded collection; the seed remains the fallback when no assets are
    observable.
    """
    derived = derive_ownership_gaps()
    if derived is not None:
        return derived
    return load_records("ownership_gaps", OwnershipGap)


@router.get("/mission/governance/audit")
def get_mission_audit_events() -> list[AuditEvent]:
    """Return the immutable governance action log, newest first."""
    return load_records("audit_events", AuditEvent)


@router.get("/mission/governance/summary")
def get_mission_governance_summary() -> list[SummaryMetricRow]:
    """Return the Governance summary band."""
    return load_records("governance_summary", SummaryMetricRow)


@router.get("/mission/governance/publication-plan/{dataset_id}")
def get_mission_publication_plan(dataset_id: str) -> PublicationPlan:
    """Return the resolved publication plan for a dataset."""
    record = find_by("publication_plan", PublicationPlan, dataset_id=dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No publication plan for {dataset_id}")
    return record
