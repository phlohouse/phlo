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
    """V1 step-up challenge that accepts the current session as sufficient.

    This is a placeholder implementation that accepts the current session
    without additional verification. Future versions will integrate with
    MFA providers for true step-up authentication.
    """

    def challenge(self, session: AuthenticatedSession) -> StepUpResult:
        """Accept the current session as sufficient for step-up.

        Returns a successful ``StepUpResult`` with "session" assurance level.
        """
        return StepUpResult(
            success=True,
            assurance_level="session",
            message="Current session accepted as sufficient for signature",
        )
