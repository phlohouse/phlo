"""Access governance primitives for regulated deployments.

Provides core access governance concepts including access reviews,
separation of duties checks, and compliance attestations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class AccessReviewStatus(StrEnum):
    """Status of an access review."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AccessReviewType(StrEnum):
    """Type of access review."""

    INITIAL = "initial"
    PERIODIC = "periodic"
    EMERGENCY = "emergency"
    CERTIFICATION = "certification"


@dataclass(frozen=True, kw_only=True)
class AccessReviewer:
    """A reviewer assigned to an access review."""

    subject: str
    name: str | None = None
    email: str | None = None


@dataclass(frozen=True, kw_only=True)
class AccessReview:
    """An access review for a principal's permissions.

    Access reviews are a core compliance requirement for regulated
    deployments, ensuring that access rights are periodically validated
    by appropriate reviewers.
    """

    review_id: str = field(default_factory=lambda: str(uuid4()))
    principal_subject: str
    principal_type: str
    resource_type: str
    resource_id: str
    action: str
    review_type: AccessReviewType
    status: AccessReviewStatus = AccessReviewStatus.PENDING
    reviewers: tuple[AccessReviewer, ...] = ()
    requested_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    decided_at: str | None = None
    decided_by: str | None = None
    justification: str | None = None
    notes: str | None = None
    compliance_domain: str | None = None


@dataclass(frozen=True, kw_only=True)
class SeparationOfDutiesViolation:
    """A separation of duties violation detected during access review."""

    principal_subject: str
    conflicting_roles: tuple[str, ...]
    policy_id: str
    message: str


class SeparationOfDutiesPolicy:
    """Policy defining incompatible role combinations.

    Separation of duties (SoD) policies prevent a single individual
    from having access that violates compliance requirements.
    For example, someone who can approve payments should not also
    be able to initiate payments.
    """

    def __init__(
        self,
        policy_id: str,
        description: str,
        conflicting_roles: tuple[str, ...],
        severity: str = "high",
    ) -> None:
        self.policy_id = policy_id
        self.description = description
        self.conflicting_roles = conflicting_roles
        self.severity = severity

    def check_violation(self, roles: tuple[str, ...]) -> SeparationOfDutiesViolation | None:
        """Return a SeparationOfDutiesViolation when two or more conflicting
        roles are present, None otherwise.
        """
        conflicting = set(self.conflicting_roles) & set(roles)
        if len(conflicting) >= 2:
            return SeparationOfDutiesViolation(
                principal_subject="",
                conflicting_roles=tuple(conflicting),
                policy_id=self.policy_id,
                message=f"Roles {conflicting} violate SoD policy {self.policy_id}: {self.description}",
            )
        return None


DEFAULT_SOD_POLICIES: list[SeparationOfDutiesPolicy] = [
    SeparationOfDutiesPolicy(
        policy_id="sod-data-admin",
        description="Data admin and data write access should not be held by same principal",
        conflicting_roles=("data_admin", "data_write"),
    ),
    SeparationOfDutiesPolicy(
        policy_id="sod-payment",
        description="Payment approval and initiation should be separate roles",
        conflicting_roles=("payment_approver", "payment_initiator"),
    ),
]


@dataclass(frozen=True, kw_only=True)
class ComplianceAttestation:
    """An attestation record for compliance evidence.

    Attestations are signed statements by principals confirming
    they have reviewed and accept their access rights.
    """

    attestation_id: str = field(default_factory=lambda: str(uuid4()))
    principal_subject: str
    reviewer_subject: str
    attestations: tuple[str, ...]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str | None = None
    signature_hash: str | None = None


def check_separation_of_duties(
    principal_subject: str,
    roles: tuple[str, ...],
    policies: list[SeparationOfDutiesPolicy] | None = None,
) -> list[SeparationOfDutiesViolation]:
    """Check a principal's roles against SoD policies (defaults when none
    are given) and return the violations, empty if none.
    """
    if policies is None:
        policies = DEFAULT_SOD_POLICIES

    violations: list[SeparationOfDutiesViolation] = []
    for policy in policies:
        violation = policy.check_violation(roles)
        if violation:
            import dataclasses

            violations.append(
                dataclasses.replace(
                    violation,
                    principal_subject=principal_subject,
                )
            )
    return violations
