"""Access recertification campaigns for compliance.

Handles periodic access review campaigns where reviewers must
certify that principals still require their current access rights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from phlo.compliance.governance.types import (
        AccessReview,
        ComplianceAttestation,
    )


class CampaignStatus(StrEnum):
    """Status of a recertification campaign."""

    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"


@dataclass(frozen=True, kw_only=True)
class RecertificationCampaign:
    """A recertification campaign for access rights."""

    campaign_id: str = field(default_factory=lambda: str(uuid4()))
    name: str
    description: str | None = None
    compliance_domain: str | None = None
    status: CampaignStatus = CampaignStatus.DRAFT
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    deadline: str | None = None
    completed_at: str | None = None
    reviews: tuple[str, ...] = ()
    reviewer_count: int = 0
    completion_rate: float = 0.0


@dataclass
class CampaignSummary:
    """Summary statistics for a recertification campaign."""

    campaign_id: str
    total_reviews: int
    completed: int
    pending: int
    denied: int
    overdue: int
    completion_rate: float


class RecertificationManager:
    """Manages access recertification campaigns.

    In regulated deployments, access rights must be periodically
    reviewed and certified by appropriate reviewers.
    """

    def __init__(self) -> None:
        self._campaigns: dict[str, RecertificationCampaign] = {}

    def create_campaign(
        self,
        name: str,
        description: str | None = None,
        compliance_domain: str | None = None,
        deadline_days: int = 30,
    ) -> RecertificationCampaign:
        """Create a recertification campaign with a deadline the given number
        of days out (no deadline when deadline_days is not positive)."""
        deadline = None
        if deadline_days > 0:
            deadline_dt = datetime.now(UTC) + timedelta(days=deadline_days)
            deadline = deadline_dt.isoformat()

        campaign = RecertificationCampaign(
            name=name,
            description=description,
            compliance_domain=compliance_domain,
            deadline=deadline,
        )
        self._campaigns[campaign.campaign_id] = campaign
        return campaign

    def add_review(
        self,
        campaign_id: str,
        review: AccessReview,
    ) -> None:
        """Add a review to a campaign and activate it.

        Raises ValueError when the campaign ID is unknown.
        """
        if campaign_id not in self._campaigns:
            raise ValueError(f"Campaign not found: {campaign_id}")

        campaign = self._campaigns[campaign_id]
        new_reviews = (*campaign.reviews, review.review_id)
        object.__setattr__(campaign, "reviews", new_reviews)
        object.__setattr__(campaign, "status", CampaignStatus.ACTIVE)

    def get_summary(self, campaign_id: str) -> CampaignSummary | None:
        """Return summary statistics for a campaign, or None if not found."""
        if campaign_id not in self._campaigns:
            return None

        campaign = self._campaigns[campaign_id]
        total = len(campaign.reviews)

        completed = 0
        pending = 0
        denied = 0
        overdue = 0

        for review_id in campaign.reviews:
            status = self._get_review_status(review_id)
            if status == "completed":
                completed += 1
            elif status == "pending":
                pending += 1
            elif status == "denied":
                denied += 1
            elif status == "overdue":
                overdue += 1

        completion_rate = completed / total if total > 0 else 0.0

        return CampaignSummary(
            campaign_id=campaign_id,
            total_reviews=total,
            completed=completed,
            pending=pending,
            denied=denied,
            overdue=overdue,
            completion_rate=completion_rate,
        )

    def _get_review_status(self, review_id: str) -> str:
        return "pending"

    def complete_campaign(self, campaign_id: str) -> None:
        """Mark a campaign as completed with a completion timestamp.

        Raises ValueError when the campaign ID is unknown.
        """
        if campaign_id not in self._campaigns:
            raise ValueError(f"Campaign not found: {campaign_id}")

        campaign = self._campaigns[campaign_id]
        object.__setattr__(campaign, "status", CampaignStatus.COMPLETED)
        object.__setattr__(campaign, "completed_at", datetime.now(UTC).isoformat())


def create_attestation(
    principal_subject: str,
    reviewer_subject: str,
    attestations: tuple[str, ...],
    expires_days: int | None = None,
) -> ComplianceAttestation:
    """Create a compliance attestation for a principal reviewed by a reviewer,
    optionally expiring after the given number of days."""
    from phlo.compliance.governance.types import ComplianceAttestation

    expires_at = None
    if expires_days is not None and expires_days > 0:
        expires_dt = datetime.now(UTC) + timedelta(days=expires_days)
        expires_at = expires_dt.isoformat()

    return ComplianceAttestation(
        principal_subject=principal_subject,
        reviewer_subject=reviewer_subject,
        attestations=attestations,
        expires_at=expires_at,
    )
