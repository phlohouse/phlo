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

from phlo_api.observatory_api.observatory import (
    _load_operations,
    _load_services,
)
from phlo_api.observatory_api.observatory_mission_control_models import (
    MissionAttentionItem,
    MissionExecutionRow,
    MissionRunDetail,
    RunArtifact,
    RunConfigurationRow,
    PlatformService,
    PlatformSummaryMetric,
    SummaryMetricRow,
)
from phlo_api.observatory_api.observatory_models import ObservatoryRun
from phlo_api.observatory_api.observatory_runs import load_runs

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


# ------------------------------------------------------------------ overview


def _elapsed(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "—"
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _run_tone(status: str) -> str:
    state = status.strip().lower()
    if state in {"success", "succeeded"}:
        return "success"
    if state in {"failure", "failed"}:
        return "danger"
    if state in {"started", "running", "queued"}:
        return "accent"
    return "muted"


def derive_overview_metrics() -> list[SummaryMetricRow] | None:
    """Build the Overview summary band from live execution state.

    Release and governance counters have no upstream source and are reported as
    unavailable rather than invented.
    """
    try:
        runs = load_runs()
        operations = _load_operations()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if not runs and not operations:
        return None

    by_status: dict[str, int] = {}
    for run in runs:
        by_status[str(run.status)] = by_status.get(str(run.status), 0) + 1

    succeeded = by_status.get("success", 0) + by_status.get("succeeded", 0)
    failed = by_status.get("failure", 0) + by_status.get("failed", 0)
    active = sum(
        count for status, count in by_status.items() if status in {"started", "running", "queued"}
    )

    assets: set[str] = set()
    for run in runs:
        for ref in run.assets:
            assets.add(ref.id)

    return [
        SummaryMetricRow(
            label="Data",
            value=f"{len(assets)} assets",
            hint="Referenced by observed runs",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Ingestion",
            value=f"{len(operations)} operations",
            hint="Reported by the orchestrator",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Quality",
            value="—",
            hint="No quality source configured",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Execution",
            value=f"{len(runs)} runs",
            hint=f"{succeeded} succeeded · {active} active · {failed} failed",
            tone="danger" if failed else "muted",
        ),
        SummaryMetricRow(
            label="Releases",
            value="—",
            hint="No release source configured",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Governance",
            value="—",
            hint="No ownership source configured",
            tone="muted",
        ),
    ]


def derive_overview_execution() -> list[MissionExecutionRow] | None:
    """Build the active-execution table from runs that are not terminal."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    active = [
        run for run in runs if str(run.status).strip().lower() in {"started", "running", "queued"}
    ]
    # An empty result is a truthful "nothing is running". Only an unavailable
    # source (handled above) falls back to the seeded read model.
    return [
        MissionExecutionRow(
            id=run.id,
            workflow=run.name,
            stage=str(run.status).replace("_", " ").title(),
            progress=(f"{len(run.assets)} assets" if run.assets else "In progress"),
            elapsed=_elapsed(run.duration_seconds),
            run_id=run.id,
        )
        for run in active
    ]


def derive_overview_attention() -> list[MissionAttentionItem] | None:
    """Surface failed runs as priority items, newest first."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    failed = [run for run in runs if str(run.status).strip().lower() in {"failure", "failed"}]
    failed.sort(key=lambda run: run.completed_at or "", reverse=True)
    return [
        MissionAttentionItem(
            id=f"attn-{run.id}",
            severity="danger",
            title=f"{run.name} failed",
            detail=(
                f"Run {run.id} · {len(run.assets)} assets · "
                f"{(run.completed_at or run.started_at or 'unknown time')}"
            ),
            action="Inspect run",
            target=f"/runs/{run.id}",
        )
        for run in failed[:5]
    ]


def derive_run(run_id: str) -> MissionRunDetail | None:
    """Build the run header and details rail from the live orchestrator."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    run = next((candidate for candidate in runs if candidate.id == run_id), None)
    if run is None:
        return None

    tone = _run_tone(str(run.status))
    assets = len(run.assets)
    checks = len(run.checks)

    return MissionRunDetail(
        id=run.id,
        workflow=run.name,
        run_id=run.id,
        status=str(run.status).replace("_", " ").title(),
        summary=_run_summary(run),
        metrics=[
            SummaryMetricRow(
                label="Execution",
                value=str(run.status).replace("_", " ").title(),
                hint="Reported by the orchestrator",
                tone=tone,
            ),
            SummaryMetricRow(
                label="Assets",
                value=str(assets),
                hint="Touched by this run",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Checks",
                value=str(checks),
                hint="Attached to this run",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Duration",
                value=_elapsed(run.duration_seconds),
                hint=f"{run.started_at or '—'} → {run.completed_at or '—'}",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Attempt",
                value="—",
                hint="Not reported by this orchestrator",
                tone="muted",
            ),
        ],
        details=[
            RunConfigurationRow(run_id=run.id, label="Run", value=run.id),
            RunConfigurationRow(run_id=run.id, label="Pipeline", value=run.name),
            RunConfigurationRow(run_id=run.id, label="Started", value=run.started_at or "—"),
            RunConfigurationRow(run_id=run.id, label="Completed", value=run.completed_at or "—"),
            RunConfigurationRow(
                run_id=run.id,
                label="Assets",
                value=", ".join(ref.id for ref in run.assets) or "—",
            ),
        ],
        consumers=[],
        artifacts=[
            RunArtifact(
                run_id=run.id,
                name=ref.id,
                size="—",
                checksum="—",
            )
            for ref in run.checks
        ],
    )


def _run_summary(run: ObservatoryRun) -> str:
    parts = [f"Run {run.id}"]
    if run.started_at:
        parts.append(f"started {run.started_at}")
    if run.completed_at:
        parts.append(f"completed {run.completed_at}")
    if run.assets:
        parts.append(f"{len(run.assets)} assets")
    return " · ".join(parts)


# NOTE: run stage breakdown is not on ObservatoryRun; it lives in the run report
# store (phlo.run_evidence). Deriving stages needs that store wired here, so the
# seeded stage timeline remains in use until it is.
