"""Provider-neutral emission helpers for authoritative run observations.

Observation ids hash identity parts so identical observations dedupe downstream
across retries and processes. Emission sits outside provider control flow: sink
failures are logged and contained at this boundary, never propagated.

Emission boundary used across the run_evidence package and exercised by the
observability test suite.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from phlo.hooks import HookCorrelation, get_hook_bus
from phlo.hooks.events import RunEvidenceObservationEvent, normalize_attempt
from phlo.logging import get_logger

logger = get_logger(__name__)


def _safe_text(value: object) -> str:
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - logging must not escape the evidence boundary
        return "<unavailable>"


# Hash the identity parts so identical observations always produce the same
# event id across retries and processes; downstream idempotency relies on it.
def _stable_id(*parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def emit_lifecycle_safely(emitter: object, method_name: str, **kwargs: Any) -> None:
    """Emit an observational lifecycle event without changing provider control flow.

    Lifecycle hooks are evidence sinks. Once provider work has started, a sink
    failure must remain distinct from the provider result, including malformed
    event construction and HookBus ``TypeError`` failures.
    """
    try:
        method = getattr(emitter, method_name)
        method(**kwargs)
    except Exception as exc:  # noqa: BLE001 - evidence boundary must contain all sink failures
        logger.error(
            "run_evidence_lifecycle_persist_failed",
            operation=_safe_text(method_name),
            error_type=type(exc).__name__,
        )


def emit_observation(
    *,
    project_id: str | None,
    run_id: str,
    attempt: int | None = 1,
    correlation_error: str | None = None,
    observation_type: str,
    status: str,
    run_status: str | None = None,
    producer: str,
    stage_id: str | None = None,
    resources: list[dict[str, Any]] | None = None,
    catalog_change: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
    event_id: str | None = None,
    identity_parts: tuple[object, ...] = (),
) -> None:
    """Emit one stable, correlated observation after provider work completes."""
    try:
        try:
            normalized_attempt = normalize_attempt(attempt)
        except ValueError:
            logger.error(
                "run_evidence_observation_correlation_gap",
                project_id=_safe_text(project_id),
                run_id=_safe_text(run_id),
                attempt=_safe_text(attempt),
                producer=_safe_text(producer),
                observation_type=_safe_text(observation_type),
                correlation_error=_safe_text(correlation_error),
                missing_evidence=["attempt"],
            )
            return
        if not project_id or not run_id:
            logger.error(
                "run_evidence_observation_correlation_gap",
                project_id=_safe_text(project_id),
                run_id=_safe_text(run_id),
                attempt=_safe_text(attempt),
                producer=_safe_text(producer),
                observation_type=_safe_text(observation_type),
                correlation_error=_safe_text(correlation_error),
                missing_evidence=[
                    name
                    for name, value in (("project_id", project_id), ("run_id", run_id))
                    if not value
                ],
            )
            return
        event = RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id=event_id
            or _stable_id(
                project_id,
                run_id,
                normalized_attempt,
                observation_type,
                *identity_parts,
            ),
            producer=producer,
            observation_type=observation_type,
            status=status,
            run_status=run_status,
            stage_id=stage_id,
            resources=resources or [],
            catalog_change=catalog_change,
            artifacts=artifacts or [],
            metrics=metrics or {},
            error=error,
            correlation=HookCorrelation(
                project_id=project_id,
                run_id=run_id,
                attempt=normalized_attempt,
            ),
        )
        get_hook_bus().emit(event)
    except Exception as exc:  # noqa: BLE001 - provider result already committed
        logger.error(
            "run_evidence_observation_persist_failed",
            project_id=_safe_text(project_id),
            run_id=_safe_text(run_id),
            attempt=_safe_text(attempt),
            producer=_safe_text(producer),
            observation_type=_safe_text(observation_type),
            error_type=type(exc).__name__,
        )
