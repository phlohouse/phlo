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
    AccessDrift,
    AuditEvent,
    OwnershipGap,
    PublicationReview,
)
from phlo_api.observatory_api.observatory_mission_control_state import load_records

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
    """Return datasets missing an ownership or contract requirement."""
    return load_records("ownership_gaps", OwnershipGap)


@router.get("/mission/governance/audit")
def get_mission_audit_events() -> list[AuditEvent]:
    """Return the immutable governance action log, newest first."""
    return load_records("audit_events", AuditEvent)
