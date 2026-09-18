"""Derive Mission Control read models from live platform substrate.

Where the substrate already answers the question, prefer it over the seeded
collection. Everything here is best-effort: a derivation returns ``None`` when
the substrate cannot answer, and the router falls back to the seeded read model
so a screen degrades to reference data instead of going blank.

Currently derived:
  - Platform services and summary (from ``/services``)

Still seeded, because nothing upstream models them yet:
  - Release candidates, publication plans, ownership, contracts, access policy
  - Backup coverage and maintenance reporting
"""

from __future__ import annotations

from collections import Counter

from phlo_api.observatory_api.observatory import _load_services
from phlo_api.observatory_api.observatory_mission_control_models import (
    PlatformService,
    PlatformSummaryMetric,
)

# Provider kinds are free-form; map the common ones to a human role label.
_ROLE_LABELS = {
    "orchestrator": "Orchestration",
    "catalog": "Catalog",
    "object_store": "Object storage",
    "query_engine": "Query engine",
    "serving": "Serving & state",
    "telemetry": "Telemetry",
    "ingestion": "Ingestion",
    "transform": "Transform",
    "bi": "BI",
    "auth": "Authentication",
    "routing": "Routing",
    "streaming": "Streaming",
}


def _is_ready(state: str) -> bool:
    return state.strip().lower() in {"ok", "healthy", "ready", "up"}


def derive_platform_services() -> list[PlatformService] | None:
    """Build the Platform service table from live service status.

    Returns ``None`` when no services are discovered so the caller can fall back
    to the seeded read model.
    """
    try:
        services = _load_services()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if not services:
        return None

    # Only services whose runtime state actually resolved. The catalog lists
    # every service the platform knows about, most of which are not part of this
    # project; reporting them as unknown-state rows is noise, not signal.
    observed = [
        service
        for service in services
        if str(getattr(service, "status", "")).strip().lower() not in {"", "unknown"}
    ]
    if not observed:
        return None

    rows: list[PlatformService] = []
    for service in observed:
        # `status` is the process state and `health.state` the readiness probe;
        # they are reported separately on purpose and never merged.
        runtime_state = str(service.status).replace("_", " ").title()
        health_state = str(getattr(service.health, "state", "unknown"))
        ready = _is_ready(health_state)
        probe = getattr(service.health, "message", None) or health_state
        rows.append(
            PlatformService(
                name=service.name,
                role=_ROLE_LABELS.get(
                    str(service.kind), str(service.kind).replace("_", " ").title()
                ),
                runtime_state=runtime_state,
                readiness_state="Ready" if ready else "Not ready",
                probe=probe,
                action="Open",
                attention=not ready,
            )
        )
    return rows


def derive_platform_summary(services: list[PlatformService]) -> list[PlatformSummaryMetric]:
    """Build the Platform summary band from the derived service rows."""
    running = Counter(row.runtime_state for row in services)
    ready = [row for row in services if row.readiness_state == "Ready"]
    unready = [row for row in services if row.readiness_state != "Ready"]
    total = len(services)
    running_count = running.get("Running", 0)

    return [
        PlatformSummaryMetric(
            label="Enabled services",
            value=f"{total} of {total}",
            hint="Discovered from the running project",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Running",
            value=f"{running_count} of {total}",
            hint="Container state observed",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Ready",
            value=f"{len(ready)} of {total}",
            hint=f"{len(unready)} readiness probe failing" if unready else "All probes passing",
            tone="warning" if unready else "success",
        ),
        PlatformSummaryMetric(
            label="Telemetry",
            value="Complete",
            hint="Service health collected",
            tone="success",
        ),
        PlatformSummaryMetric(
            label="Latest backup",
            value="—",
            hint="Not reported by this project",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Restore rehearsal",
            value="—",
            hint="Not reported by this project",
            tone="muted",
        ),
    ]
