"""Access governance primitives for regulated deployments.

Public surface for activity monitoring, break-glass approvals, dormancy
detection, access recertification, and separation-of-duties checks. The
submodules own the implementations; this package only re-exports the
curated API.
"""

from phlo.compliance.governance.activity import (
    AccessActivity,
    ActivityMonitor,
    ActivitySummary,
    record_enforcement_activity,
)
from phlo.compliance.governance.break_glass import (
    BreakGlassApproval,
    BreakGlassManager,
    BreakGlassRequest,
    BreakGlassStatus,
    create_emergency_review,
)
from phlo.compliance.governance.dormant import (
    DormancyDetector,
    DormancyThreshold,
    DormantPrincipal,
    create_dormancy_review,
)
from phlo.compliance.governance.recertification import (
    CampaignStatus,
    CampaignSummary,
    RecertificationCampaign,
    RecertificationManager,
    create_attestation,
)
from phlo.compliance.governance.types import (
    DEFAULT_SOD_POLICIES,
    AccessReview,
    AccessReviewer,
    AccessReviewStatus,
    AccessReviewType,
    ComplianceAttestation,
    SeparationOfDutiesPolicy,
    SeparationOfDutiesViolation,
    check_separation_of_duties,
)

__all__ = [
    "AccessActivity",
    "AccessReview",
    "AccessReviewStatus",
    "AccessReviewType",
    "AccessReviewer",
    "ActivityMonitor",
    "ActivitySummary",
    "BreakGlassApproval",
    "BreakGlassManager",
    "BreakGlassRequest",
    "BreakGlassStatus",
    "CampaignStatus",
    "CampaignSummary",
    "ComplianceAttestation",
    "DEFAULT_SOD_POLICIES",
    "DormancyDetector",
    "DormancyThreshold",
    "DormantPrincipal",
    "RecertificationCampaign",
    "RecertificationManager",
    "SeparationOfDutiesPolicy",
    "SeparationOfDutiesViolation",
    "check_separation_of_duties",
    "create_attestation",
    "create_dormancy_review",
    "create_emergency_review",
    "record_enforcement_activity",
]
