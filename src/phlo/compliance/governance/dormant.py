"""Dormant account detection for access governance.

Monitors principal activity and identifies accounts that may need
recertification due to prolonged inactivity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phlo.compliance.governance.types import AccessReview


@dataclass(frozen=True, kw_only=True)
class DormancyThreshold:
    """Threshold configuration for dormancy detection."""

    max_inactive_days: int = 90
    warning_days: int = 60
    review_type: str = "periodic"


@dataclass(frozen=True, kw_only=True)
class DormantPrincipal:
    """A principal identified as potentially dormant."""

    principal_subject: str
    principal_type: str
    last_activity: datetime
    days_inactive: int
    severity: str
    should_review: bool


class DormancyDetector:
    """Detects dormant principals based on activity timestamps.

    In regulated deployments, dormant accounts pose a security risk
    and typically require periodic recertification.
    """

    def __init__(self, threshold: DormancyThreshold | None = None) -> None:
        self._threshold = threshold or DormancyThreshold()

    def check_dormancy(
        self,
        principal_subject: str,
        principal_type: str,
        last_activity: datetime,
        reference_time: datetime | None = None,
    ) -> DormantPrincipal | None:
        """Check if a principal is dormant against the configured thresholds.

        Returns a DormantPrincipal (high severity past max_inactive_days,
        medium past warning_days) or None when still active.
        """
        if reference_time is None:
            reference_time = datetime.now(UTC)

        delta = reference_time - last_activity
        days_inactive = delta.days

        if days_inactive >= self._threshold.max_inactive_days:
            severity = "high"
            should_review = True
        elif days_inactive >= self._threshold.warning_days:
            severity = "medium"
            should_review = True
        else:
            return None

        return DormantPrincipal(
            principal_subject=principal_subject,
            principal_type=principal_type,
            last_activity=last_activity,
            days_inactive=days_inactive,
            severity=severity,
            should_review=should_review,
        )

    def check_batch(
        self,
        principals: list[dict],
        reference_time: datetime | None = None,
    ) -> list[DormantPrincipal]:
        """Check multiple principals for dormancy, returning those requiring review."""
        results: list[DormantPrincipal] = []
        for p in principals:
            dormant = self.check_dormancy(
                principal_subject=p["subject"],
                principal_type=p["type"],
                last_activity=p["last_activity"],
                reference_time=reference_time,
            )
            if dormant:
                results.append(dormant)
        return results


def create_dormancy_review(
    dormant: DormantPrincipal,
    review_type: str | None = None,
) -> AccessReview:
    """Create an AccessReview for a dormant principal, ready for processing."""
    from phlo.compliance.governance.types import (
        AccessReview,
        AccessReviewStatus,
        AccessReviewType,
    )

    rt = review_type or "periodic"
    try:
        review_type_enum = AccessReviewType(rt)
    except ValueError:
        review_type_enum = AccessReviewType.PERIODIC

    return AccessReview(
        principal_subject=dormant.principal_subject,
        principal_type=dormant.principal_type,
        resource_type="account",
        resource_id=dormant.principal_subject,
        action="access.recertify",
        review_type=review_type_enum,
        status=AccessReviewStatus.PENDING,
        justification=f"Dormant account detected: {dormant.days_inactive} days inactive",
        notes=f"Last activity: {dormant.last_activity.isoformat()}",
    )
