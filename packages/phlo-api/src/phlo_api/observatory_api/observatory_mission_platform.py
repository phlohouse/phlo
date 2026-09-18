"""Platform read models: service readiness, backup coverage and maintenance.

Runtime state and readiness state are exposed separately and never merged: a
container can be running while its readiness probe fails, and conflating the two
is the failure mode this surface exists to prevent. Diagnostics carry an explicit
staleness flag so a degraded provider can be labelled "last confirmed" rather
than silently showing figures that may be out of date.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
    CoverageRow,
    PlatformService,
    PlatformSummaryMetric,
    ServiceDiagnostics,
    ServiceProbeFact,
)
from phlo_api.observatory_api.observatory_mission_control_sources import (
    derive_platform_services,
    derive_platform_summary,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    find_record,
    load_records,
)

router = APIRouter()


@router.get("/mission/platform/summary")
def get_mission_platform_summary() -> list[PlatformSummaryMetric]:
    """Return the Platform summary band, preferring live service state."""
    derived = derive_platform_services()
    if derived:
        return derive_platform_summary(derived)

    services = load_records("platform_services", PlatformService)
    diagnostics = load_records("platform_diagnostics", ServiceDiagnostics)
    unready = [row for row in services if row.readiness_state != "Ready"]
    degraded = [row for row in diagnostics if row.stale]
    return [
        PlatformSummaryMetric(
            label="Enabled services",
            value=f"{len(services)} of 16",
            hint="4 discovered, not enabled",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Running",
            value=f"{len(services)} of {len(services)}",
            hint="Container state observed",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Ready",
            value=f"{len(services) - len(unready)} of {len(services)}",
            hint=f"{len(unready)} readiness probe failing" if unready else "All probes passing",
            tone="warning" if unready else "success",
        ),
        PlatformSummaryMetric(
            label="Telemetry",
            value="Partial" if degraded else "Complete",
            hint="Loki log queries unavailable" if degraded else "All signals flowing",
            tone="warning" if degraded else "success",
        ),
        PlatformSummaryMetric(
            label="Latest backup",
            value="03:00 UTC",
            hint="4/4 contributors complete",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Restore rehearsal",
            value="Verified",
            hint="11 Sep · Isolated target",
            tone="success",
        ),
    ]


@router.get("/mission/platform/services")
def get_mission_platform_services() -> list[PlatformService]:
    """Return enabled services with runtime and readiness reported separately.

    Live service status wins; the seeded collection is the fallback so the
    screen still renders when the project is not running.
    """
    derived = derive_platform_services()
    if derived:
        return derived
    return load_records("platform_services", PlatformService)


@router.get("/mission/platform/services/{service_id}")
def get_mission_service_diagnostics(service_id: str) -> ServiceDiagnostics:
    """Return probe state for a service.

    A curated diagnostics record wins; otherwise the live service row is
    described directly, so the rail always reflects the environment rather than
    404-ing for a service the reference fixture never knew about.
    """
    record = find_record("platform_diagnostics", ServiceDiagnostics, service_id)
    if record is not None:
        return record

    derived = derive_platform_services() or []
    row = next((item for item in derived if item.name == service_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No diagnostics for service {service_id}")

    return ServiceDiagnostics(
        id=row.name,
        name=row.name,
        readiness_state=row.readiness_state,
        summary=(
            f"{row.role} · runtime {row.runtime_state} · readiness {row.readiness_state.lower()}."
        ),
        facts=[
            ServiceProbeFact(label="Runtime", value=row.runtime_state),
            ServiceProbeFact(label="Readiness", value=row.readiness_state),
            ServiceProbeFact(label="Probe", value=row.probe),
            ServiceProbeFact(label="Role", value=row.role),
        ],
        dependencies=[],
        dependency_note=("Dependency paths are not reported by this service."),
        capability_checks="Not reported",
        capabilities=[],
    )


@router.get("/mission/platform/backup")
def get_mission_backup_coverage() -> list[CoverageRow]:
    """Return backup coverage per contributor."""
    return load_records("backup_coverage", CoverageRow)


@router.get("/mission/platform/maintenance")
def get_mission_maintenance() -> list[CoverageRow]:
    """Return maintenance and recovery status."""
    return load_records("maintenance", CoverageRow)
