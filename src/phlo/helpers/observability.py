"""Observability and alerting helpers for workflow code.

All emission is best-effort: metrics and alerts go through resolved
capabilities and are silently skipped when no backend or sink exists.
run_timer always emits a duration metric, including on failure, and
re-raises the original exception.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from phlo.capabilities import resolve_capability
from phlo.logging import log_event


def emit_metric(
    name: str,
    value: float,
    *,
    unit: str | None = None,
    payload: dict[str, Any] | None = None,
    backend: Any = None,
) -> None:
    """Emit a metric when an observability backend is available."""
    provider = backend
    if provider is None:
        resolution = resolve_capability("observability_backend")
        provider = resolution.provider if resolution else None
    if provider is not None and hasattr(provider, "emit_metric"):
        provider.emit_metric(name=name, value=value, unit=unit, payload=payload or {})


def log_asset_event(
    logger: Any,
    event: str,
    *,
    asset_key: str | None = None,
    partition_key: str | None = None,
    level: str = "info",
    **metadata: Any,
) -> None:
    """Log a structured asset event."""
    log_event(
        logger,
        level,
        event,
        asset_key=asset_key,
        partition_key=partition_key,
        **metadata,
    )


@contextmanager
def run_timer(
    name: str,
    *,
    logger: Any = None,
    metric_name: str | None = None,
    **metadata: Any,
):
    """Time a workflow block and emit logs/metrics."""
    start = time.perf_counter()
    try:
        yield
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        if logger is not None:
            log_event(logger, "error", f"{name}_failed", duration_ms=duration_ms, **metadata)
        emit_metric(metric_name or f"{name}.duration", duration_ms, unit="ms", payload=metadata)
        raise
    else:
        duration_ms = (time.perf_counter() - start) * 1000
        if logger is not None:
            log_event(logger, "info", f"{name}_succeeded", duration_ms=duration_ms, **metadata)
        emit_metric(metric_name or f"{name}.duration", duration_ms, unit="ms", payload=metadata)


def record_rows_processed(count: int, *, asset_key: str | None = None) -> None:
    """Emit a standard rows-processed metric."""
    emit_metric("phlo.rows_processed", count, unit="rows", payload={"asset_key": asset_key})


def alert_on_failure(title: str, exc: Exception, *, severity: str = "critical") -> bool:
    """Send a failure alert through the active alert sink when available."""
    resolution = resolve_capability("alert_sink")
    if resolution is None or not hasattr(resolution.provider, "send_alert"):
        return False
    resolution.provider.send_alert(title=title, message=str(exc), severity=severity)
    return True
