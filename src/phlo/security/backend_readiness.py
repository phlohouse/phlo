"""Neutral backend security readiness contract (ADR 0047 §5, §7.2).

Every blessed backend registers a provider-owned, read-only ``inspect()``
through the ``backend_readiness`` family. A missing adapter, ``failed``,
or ``unavailable`` blocks production readiness.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from phlo.security.readiness import (
    BackendReadinessProvider,
    BackendReadinessResult,
    BackendReadinessState,
)

# The blessed backends that must each register a readiness adapter.
REQUIRED_BACKENDS = ("postgres", "trino", "minio", "nessie")


@dataclass(frozen=True, slots=True)
class BackendReadinessSpec:
    """Capability spec binding a backend name to its read-only inspector."""

    name: str
    provider: BackendReadinessProvider
    metadata: Mapping[str, Any] = field(default_factory=dict)


def observe_policy_convergence(backend_name: str) -> BackendReadinessResult | None:
    """Verify a live backend's managed grant/policy state against the
    canonical RBAC model via its governance backend and compiler.

    Returns ``None`` when the evidence path is not wired — no governance
    backend registered or no canonical policy resolvable — so callers keep
    their reference-level result. A reachable backend that fails to list
    state yields ``unavailable``; drift yields ``failed``; a verified match
    yields ``passed``.
    """
    from phlo.capabilities.resolver import resolve_capability
    from phlo.rbac.compiler import CompilerContext
    from phlo.rbac.registry import get_compiler
    from phlo.security.validation import _project_rbac_loader

    resolution = resolve_capability("governance_backend", backend_name)
    if resolution is None:
        return None
    try:
        rbac = _project_rbac_loader().load()
    except Exception:
        return None
    if not rbac:
        return None
    compiler = get_compiler(backend_name, backend=resolution.provider)
    if compiler is None:
        return None
    probe = getattr(resolution.provider, "probe", None)
    if callable(probe):
        try:
            reachable = bool(probe())
        except Exception:
            reachable = False
        if not reachable:
            reason = getattr(resolution.provider, "probe_reason", None)
            detail = reason() if callable(reason) else ""
            return BackendReadinessResult(
                backend=backend_name,
                state=BackendReadinessState.UNAVAILABLE,
                reason_code=detail or "backend_unreachable",
                message=(
                    f"{backend_name} could not be observed for policy evidence"
                    + (f" ({detail.replace('_', ' ')})" if detail else "")
                ),
                evidence_source="governance backend",
            )
    context = CompilerContext(environment="production", backend_name=backend_name)
    try:
        desired = compiler.compile(rbac, context)
        current = compiler.read_current_state(context)
        verified = compiler.verify(rbac, context)
    except Exception as exc:
        return BackendReadinessResult(
            backend=backend_name,
            state=BackendReadinessState.UNAVAILABLE,
            reason_code="inspection_failed",
            message=f"{backend_name} policy state could not be observed: {exc}",
            evidence_source="governance backend",
        )
    desired_digest = _sha256_hex("\n".join(sorted(a.name for a in desired)).encode("utf-8"))
    observed_digest = _sha256_hex("\n".join(sorted(a.name for a in current)).encode("utf-8"))
    if not verified.in_sync:
        drift = tuple(
            {"direction": direction, "artifact": artifact.name}
            for direction, artifacts in (
                ("missing", verified.missing),
                ("extra", verified.extra),
                ("mismatched", verified.mismatched),
            )
            for artifact in artifacts
        )
        return BackendReadinessResult(
            backend=backend_name,
            state=BackendReadinessState.FAILED,
            reason_code="policy_drift",
            message=(
                f"{backend_name} managed policy state differs from the canonical "
                f"model: {len(verified.missing)} missing, {len(verified.extra)} extra, "
                f"{len(verified.mismatched)} mismatched"
            ),
            desired_policy_digest=desired_digest,
            observed_policy_digest=observed_digest,
            drift=drift,
            evidence_source="governance backend",
        )
    return BackendReadinessResult(
        backend=backend_name,
        state=BackendReadinessState.PASSED,
        reason_code="policy_converged",
        message=f"{backend_name} managed policy state matches the canonical model",
        desired_policy_digest=desired_digest,
        observed_policy_digest=observed_digest,
        evidence_source="governance backend",
    )


def _sha256_hex(payload: bytes) -> str:
    """Return the hex sha256 of payload for deterministic evidence digests."""
    import hashlib

    return hashlib.sha256(payload).hexdigest()


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
