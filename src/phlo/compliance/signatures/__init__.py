"""Electronic signature service for regulated deployments.

Provides electronic signature functionality including:
- Signature request/record types
- Step-up authentication challenges
- Signature service for recording and verifying signatures
"""

from phlo.compliance.signatures.service import (
    DEFAULT_CRITICAL_ACTIONS,
    SignatureService,
    SignatureServiceConfig,
)
from phlo.compliance.signatures.step_up import (
    RecentMfaClaimsChallenge,
    SessionConfirmChallenge,
    StepUpAuthChallenge,
    StepUpResult,
)
from phlo.compliance.signatures.types import (
    SignatureMeaning,
    SignatureRecord,
    SignatureRequest,
)

__all__ = [
    "DEFAULT_CRITICAL_ACTIONS",
    "RecentMfaClaimsChallenge",
    "SessionConfirmChallenge",
    "SignatureMeaning",
    "SignatureRecord",
    "SignatureRequest",
    "SignatureService",
    "SignatureServiceConfig",
    "StepUpAuthChallenge",
    "StepUpResult",
]
