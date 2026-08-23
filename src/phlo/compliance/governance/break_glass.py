"""Break-glass emergency access handling.

Provides expedited access review workflows for urgent situations
where normal approval timelines cannot be met.
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
    )


class BreakGlassStatus(StrEnum):
    """Status of a break-glass request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True, kw_only=True)
class BreakGlassRequest:
    """An emergency break-glass access request.

    Break-glass allows urgent access when normal approval
    timelines would cause unacceptable delays.
    """

    request_id: str = field(default_factory=lambda: str(uuid4()))
    principal_subject: str
    principal_type: str
    resource_type: str
    resource_id: str
    action: str
    justification: str
    urgency: str = "high"
    status: BreakGlassStatus = BreakGlassStatus.PENDING
    requested_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    approved_at: str | None = None
    approved_by: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None
    revocation_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class BreakGlassApproval:
    """Approval details for a break-glass request."""

    request_id: str
    approved_by: str
    approved_at: str
    validity_hours: int
    conditions: str | None = None


class BreakGlassManager:
    """Manages break-glass emergency access requests.

    Break-glass requests bypass normal approval workflows but
    require post-hoc review and have time-limited validity.
    """

    DEFAULT_VALIDITY_HOURS = 24

    def __init__(self, default_validity_hours: int | None = None) -> None:
        self._requests: dict[str, BreakGlassRequest] = {}
        self._default_validity_hours = default_validity_hours or self.DEFAULT_VALIDITY_HOURS

    def create_request(
        self,
        principal_subject: str,
        principal_type: str,
        resource_type: str,
        resource_id: str,
        action: str,
        justification: str,
        urgency: str = "high",
    ) -> BreakGlassRequest:
        """Create a new break-glass request.

        Records the requesting principal, target resource and action,
        business justification, and urgency level ("high" or "critical").
        Returns the created BreakGlassRequest.
        """
        request = BreakGlassRequest(
            principal_subject=principal_subject,
            principal_type=principal_type,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            justification=justification,
            urgency=urgency,
        )
        self._requests[request.request_id] = request
        return request

    def approve(
        self,
        request_id: str,
        approved_by: str,
        validity_hours: int | None = None,
        conditions: str | None = None,
    ) -> BreakGlassApproval:
        """Approve a break-glass request.

        Records approved_by and an expiration after validity_hours hours
        (defaulting to the configured default), with optional conditions.
        Returns the BreakGlassApproval details. Raises ValueError when the
        request is not found or not pending.
        """
        if request_id not in self._requests:
            raise ValueError(f"Request not found: {request_id}")

        request = self._requests[request_id]
        if request.status != BreakGlassStatus.PENDING:
            raise ValueError(f"Request not pending: {request.status}")

        hours = validity_hours or self._default_validity_hours
        expires_at = datetime.now(UTC) + timedelta(hours=hours)

        object.__setattr__(request, "status", BreakGlassStatus.APPROVED)
        approved_at = datetime.now(UTC).isoformat()
        object.__setattr__(request, "approved_at", approved_at)
        object.__setattr__(request, "approved_by", approved_by)
        object.__setattr__(request, "expires_at", expires_at.isoformat())

        return BreakGlassApproval(
            request_id=request_id,
            approved_by=approved_by,
            approved_at=approved_at,
            validity_hours=hours,
            conditions=conditions,
        )

    def deny(self, request_id: str, denied_by: str, reason: str) -> None:
        """Deny a break-glass request on behalf of denied_by with a reason.

        Raises ValueError when the request is not found or not pending.
        """
        if request_id not in self._requests:
            raise ValueError(f"Request not found: {request_id}")

        request = self._requests[request_id]
        if request.status != BreakGlassStatus.PENDING:
            raise ValueError(f"Request not pending: {request.status}")

        object.__setattr__(request, "status", BreakGlassStatus.DENIED)

    def revoke(
        self,
        request_id: str,
        revoked_by: str,
        reason: str,
    ) -> None:
        """Revoke an approved break-glass request on behalf of revoked_by
        with a reason.

        Raises ValueError when the request is not found or not approved.
        """
        if request_id not in self._requests:
            raise ValueError(f"Request not found: {request_id}")

        request = self._requests[request_id]
        if request.status != BreakGlassStatus.APPROVED:
            raise ValueError(f"Request not approved: {request.status}")

        object.__setattr__(request, "status", BreakGlassStatus.REVOKED)
        object.__setattr__(request, "revoked_at", datetime.now(UTC).isoformat())
        object.__setattr__(request, "revocation_reason", reason)

    def get_request(self, request_id: str) -> BreakGlassRequest | None:
        """Get a break-glass request by ID.

        Returns the BreakGlassRequest, or None when not found.
        """
        return self._requests.get(request_id)

    def is_valid(self, request_id: str) -> bool:
        """Check if an approved request is still valid.

        Returns True only when the request exists, is approved, and has
        not expired.
        """
        request = self._requests.get(request_id)
        if request is None or request.status != BreakGlassStatus.APPROVED:
            return False

        if request.expires_at is None:
            return True

        expires = datetime.fromisoformat(request.expires_at)
        return datetime.now(UTC) < expires


def create_emergency_review(request: BreakGlassRequest) -> AccessReview:
    """Create a post-hoc emergency AccessReview from a break-glass request
    for audit purposes.
    """
    from phlo.compliance.governance.types import (
        AccessReview,
        AccessReviewStatus,
        AccessReviewType,
    )

    return AccessReview(
        principal_subject=request.principal_subject,
        principal_type=request.principal_type,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        action=request.action,
        review_type=AccessReviewType.EMERGENCY,
        status=AccessReviewStatus.APPROVED
        if request.status == BreakGlassStatus.APPROVED
        else AccessReviewStatus.DENIED,
        justification=request.justification,
        notes=f"Break-glass request {request.request_id}",
    )
