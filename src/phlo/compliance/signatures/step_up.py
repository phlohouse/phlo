"""Step-up authentication challenge protocol.

Defines the protocol for step-up authentication challenges (e.g., MFA re-verification)
that may be required for electronic signatures in regulated deployments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phlo.capabilities.interfaces import AuthenticatedSession


@dataclass(frozen=True)
class StepUpResult:
    """Result of a step-up authentication challenge."""

    success: bool
    """Whether the step-up was successful."""

    assurance_level: str = "session"
    """The assurance level achieved (e.g., "session", "mfa", "re-authenticated")."""

    message: str | None = None
    """Optional message explaining the result."""


class StepUpAuthChallenge:
    """Protocol for step-up authentication challenges.

    Implementations handle the actual step-up authentication mechanism,
    such as MFA verification, re-authentication, etc.
    """

    def challenge(self, session: AuthenticatedSession) -> StepUpResult:
        """Present a step-up challenge for the given session and return the result.

        The returned ``StepUpResult`` reports success and the assurance
        level achieved.
        """
        raise NotImplementedError


class SessionConfirmChallenge(StepUpAuthChallenge):
    """Fail-closed challenge used until a real step-up verifier is configured.

    A current session alone cannot demonstrate re-authentication or MFA.
    Future versions can replace this with a verifier-backed challenge.
    """

    def challenge(self, session: AuthenticatedSession) -> StepUpResult:
        """Deny because the current session did not perform verification.

        Returns a failed ``StepUpResult`` with no authentication assurance.
        """
        return StepUpResult(
            success=False,
            assurance_level="none",
            message="No step-up verification mechanism is configured",
        )
