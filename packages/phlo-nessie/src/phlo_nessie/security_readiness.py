"""Provider-owned nessie security readiness (read-only).

Inspects only locally inspectable facts (configuration present, non-default).
Authoritative grant/audit observation requires a live backend and is reported
``unavailable`` until that evidence is obtainable; ``unavailable`` and
``failed`` both block production readiness.
"""

from __future__ import annotations

import os

from phlo.security.backend_readiness import (
    BackendReadinessResult,
    BackendReadinessState,
)

REQUIRED_REFERENCES = ["QUARKUS_DATASOURCE_USERNAME", "QUARKUS_DATASOURCE_PASSWORD"]


class NessieReadinessProvider:
    """Read-only readiness inspector for nessie."""

    backend_name = "nessie"

    def inspect(self) -> BackendReadinessResult:
        missing = [ref for ref in REQUIRED_REFERENCES if not os.environ.get(ref, "").strip()]
        if missing:
            return BackendReadinessResult(
                backend="nessie",
                state=BackendReadinessState.FAILED,
                reason_code="config_missing",
                message="nessie readiness: required credential references missing: "
                + ", ".join(sorted(missing)),
                evidence_source="declared configuration",
            )
        return BackendReadinessResult(
            backend="nessie",
            state=BackendReadinessState.UNAVAILABLE,
            reason_code="evidence_unavailable",
            message="nessie grant/audit evidence requires a live backend inspection",
            evidence_source="declared configuration",
        )

    def plan(self) -> list[dict] | None:
        return None
