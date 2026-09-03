"""Neutral backend security readiness contract (ADR 0047 §5, §7.2).

Every blessed backend registers a provider-owned readiness result through the
``backend_readiness`` capability family. ``inspect()`` is strictly read-only
and returns one sanitized result. A missing required adapter, a ``failed``
result, or an ``unavailable`` result blocks production readiness; a provider
never reports a fact it cannot authoritatively observe.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class BackendReadinessState(StrEnum):
    """Closed readiness state for one backend (ADR 0047 §5)."""

    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


# The blessed backends that must each register a readiness adapter.
REQUIRED_BACKENDS = ("postgres", "trino", "minio", "nessie")


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

    def plan(self) -> list[Mapping[str, Any]] | None:
        """Optionally describe planned provider-native changes (never applied)."""
        return None


@dataclass(frozen=True, slots=True)
class BackendReadinessSpec:
    """Capability spec binding a backend name to its read-only inspector."""

    name: str
    provider: BackendReadinessProvider
    metadata: Mapping[str, Any] = field(default_factory=dict)


def stamp(result: BackendReadinessResult) -> BackendReadinessResult:
    """Attach the observation time to a result for deterministic evidence."""
    if result.observation_time:
        return result
    return BackendReadinessResult(
        backend=result.backend,
        state=result.state,
        reason_code=result.reason_code,
        message=result.message,
        desired_policy_digest=result.desired_policy_digest,
        observed_policy_digest=result.observed_policy_digest,
        drift=result.drift,
        evidence_source=result.evidence_source,
        observation_time=datetime.now(UTC).isoformat(),
    )
