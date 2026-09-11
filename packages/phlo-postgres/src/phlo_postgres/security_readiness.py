"""Provider-owned postgres security readiness (read-only).

Inspects locally inspectable facts (configuration present, non-default),
then observes managed-grant convergence against the canonical RBAC model
through the postgres governance backend when that path is wired. When no
live observation is possible the result stays ``unavailable``;
``unavailable`` and ``failed`` both block production readiness.
"""

from __future__ import annotations

import os

from phlo.security.backend_readiness import (
    BackendReadinessResult,
    BackendReadinessState,
    observe_policy_convergence,
    stamp,
)

REQUIRED_REFERENCES = ["POSTGRES_USER", "POSTGRES_PASSWORD"]


class PostgresReadinessProvider:
    """Read-only readiness inspector for postgres."""

    backend_name = "postgres"

    def inspect(self) -> BackendReadinessResult:
        missing = [ref for ref in REQUIRED_REFERENCES if not os.environ.get(ref, "").strip()]
        if missing:
            return BackendReadinessResult(
                backend="postgres",
                state=BackendReadinessState.FAILED,
                reason_code="config_missing",
                message="postgres readiness: required credential references missing: "
                + ", ".join(sorted(missing)),
                evidence_source="declared configuration",
            )
        observed = observe_policy_convergence("postgres")
        if observed is not None:
            return stamp(observed)
        return BackendReadinessResult(
            backend="postgres",
            state=BackendReadinessState.UNAVAILABLE,
            reason_code="evidence_unavailable",
            message="postgres grant/audit evidence requires a live backend inspection",
            evidence_source="declared configuration",
        )

    def plan(self) -> list[dict] | None:
        return None
