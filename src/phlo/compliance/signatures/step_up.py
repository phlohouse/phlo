"""Step-up authentication challenge protocol.

Defines the protocol for step-up authentication challenges (e.g., MFA re-verification)
that may be required for electronic signatures in regulated deployments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
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


class RecentMfaClaimsChallenge(StepUpAuthChallenge):
    """Accept only a recently authenticated user session with verified MFA claims.

    Claims are trusted here only because they are supplied by the configured
    JWT provider after signature and expiry validation (and issuer/audience
    validation when configured). A normal session, service token, or
    client-provided request field is not a step-up proof.
    """

    def __init__(self, max_age_seconds: int = 300) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._max_age_seconds = max_age_seconds

    def challenge(self, session: AuthenticatedSession) -> StepUpResult:
        """Verify a recent ``auth_time`` and an IdP-asserted ``mfa`` method."""
        if (
            session.provider_name != "jwt"
            or session.auth_method != "bearer_token"
            or session.principal.principal_type != "user"
            or not session.attributes.get("jwt_issuer")
            or not session.attributes.get("jwt_audience")
            or session.attributes.get("jwt_issuer_validated") != "true"
            or session.attributes.get("jwt_audience_validated") != "true"
        ):
            return StepUpResult(
                False,
                "none",
                "A human JWT with configured issuer and audience validation is required",
            )

        claims = session.principal.claims
        auth_time = claims.get("auth_time")
        amr = claims.get("amr")
        if (
            isinstance(auth_time, bool)
            or not isinstance(auth_time, (int, float))
            or not math.isfinite(auth_time)
            or not isinstance(amr, list)
            or "mfa" not in amr
        ):
            return StepUpResult(False, "none", "Recent MFA authentication is required")

        age = datetime.now(UTC).timestamp() - auth_time
        if age < 0 or age > self._max_age_seconds:
            return StepUpResult(False, "none", "MFA authentication is stale")

        return StepUpResult(True, "mfa")
