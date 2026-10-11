"""Read-only backend readiness contracts, independent of policy inspection."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class BackendReadinessState(StrEnum):
    """Closed readiness state for one backend (ADR 0047 §5)."""

    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class BackendReadinessResult:
    """Sanitized, JSON-safe readiness result for one backend."""

    backend: str
    state: BackendReadinessState
    reason_code: str
    message: str
    desired_policy_digest: str = ""
    observed_policy_digest: str = ""
    drift: tuple[Mapping[str, Any], ...] = ()
    evidence_source: str = ""
    observation_time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "message": self.message,
            "desired_policy_digest": self.desired_policy_digest,
            "observed_policy_digest": self.observed_policy_digest,
            "drift": [dict(entry) for entry in self.drift],
            "evidence_source": self.evidence_source,
            "observation_time": self.observation_time,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)


class BackendReadinessProvider(Protocol):
    """A provider-owned, read-only backend readiness inspector.

    ``inspect()`` must not mutate policy, grants, credentials, or configuration.
    An optional ``plan()`` may describe provider-native changes without applying
    them; it is never called by readiness evaluation.
    """

    backend_name: str

    def inspect(self) -> BackendReadinessResult:
        """Return the authoritative readiness result for this backend."""
        ...

    def plan(self) -> Sequence[Mapping[str, Any]] | None:
        """Optionally describe planned provider-native changes (never applied)."""
        return None
