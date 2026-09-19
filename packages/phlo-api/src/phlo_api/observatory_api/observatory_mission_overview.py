"""Mission Control shell and Overview read models.

Serves the alerts inbox, environment list, attention queue and active-execution
table. These are operator-facing read models layered over the platform, so they
are namespaced under ``/mission`` to keep them distinct from the substrate
endpoints in ``observatory.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from phlo_api.api.operation_controls import require_scope
from phlo_api.run_evidence import RunEvidenceStore, get_run_evidence_store
from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    MissionAlert,
    MissionAttentionItem,
    MissionDataProduct,
    MissionEnvironment,
    MissionExecutionRow,
    MissionOverviewRail,
    MissionServiceVisibility,
    SummaryMetricRow,
)
from phlo_api.observatory_api.observatory_mission_control_mode import (
    data_mode,
    demo_envelope,
    envelope,
    evidence,
    reject_demo_mutations,
)
from phlo_api.observatory_api.observatory_mission_control_sources import (
    SERVICE_VISIBILITY_COLLECTION,
    derive_data_products,
    derive_overview_attention,
    derive_overview_execution,
    derive_overview_metrics,
    derive_overview_rail,
    read_service_visibility,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    replace_records,
    serve_collection,
    serve_sourced,
    serve_sourced_record,
)

router = APIRouter()


@router.get("/mission/overview/alerts")
def get_mission_alerts() -> ReadEnvelope[list[MissionAlert]]:
    """Return the workspace alerts inbox, newest first."""
    return serve_collection("alerts", MissionAlert)


@router.get("/mission/overview/environments")
def get_mission_environments() -> ReadEnvelope[list[MissionEnvironment]]:
    """Return the environments the workspace can be scoped to."""
    return serve_collection("environments", MissionEnvironment)


@router.get("/mission/overview/attention")
def get_mission_attention() -> ReadEnvelope[list[MissionAttentionItem]]:
    """Return priority items, preferring failures observed in live runs."""
    return serve_sourced(derive_overview_attention, "attention", MissionAttentionItem)


@router.get("/mission/overview/execution")
def get_mission_execution() -> ReadEnvelope[list[MissionExecutionRow]]:
    """Return workflows currently running or queued, from live runs when present."""
    return serve_sourced(derive_overview_execution, "execution", MissionExecutionRow)


@router.get("/mission/overview/data-products")
def get_mission_data_products(
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[MissionDataProduct]]:
    """Return released datasets with freshness and quality signals."""
    return serve_sourced(lambda: derive_data_products(store), "data_products", MissionDataProduct)


@router.get("/mission/overview/rail")
def get_mission_overview_rail() -> ReadEnvelope[MissionOverviewRail]:
    """Return the Overview right-hand rail as a single read model."""
    return serve_sourced_record(
        derive_overview_rail,
        "overview_rail",
        MissionOverviewRail,
        "No overview rail configured",
        id="rail",
    )


class ServiceVisibilityUpdate(BaseModel):
    """Body for the rail service-visibility write.

    ``shown`` lists the catalog service names the rail may display; ``null``
    clears the recorded configuration so the rail returns to showing every
    service.
    """

    shown: list[str] | None = None


@router.get("/mission/overview/rail/service-visibility")
def get_mission_service_visibility() -> ReadEnvelope[MissionServiceVisibility]:
    """Return the recorded rail service visibility, or unconfigured."""
    try:
        config = read_service_visibility()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="Observatory durable state is unavailable"
        ) from exc
    if data_mode() == "demo":
        return demo_envelope(config, source="durable-state")
    return envelope(config, evidence("live", source="durable-state"))


@router.put("/mission/overview/rail/service-visibility")
def put_mission_service_visibility(
    http_request: Request,
    payload: ServiceVisibilityUpdate,
) -> ReadEnvelope[MissionServiceVisibility]:
    """Record which catalog services the Overview rail may display.

    An empty list is a valid configuration — it renders no services. ``null``
    clears the record so visibility returns to the catalog default.
    """
    reject_demo_mutations()
    require_scope(http_request, "lakehouse:operate")
    try:
        if payload.shown is None:
            replace_records(SERVICE_VISIBILITY_COLLECTION, [])
        else:
            shown = list(dict.fromkeys(name for raw in payload.shown if (name := str(raw).strip())))
            replace_records(
                SERVICE_VISIBILITY_COLLECTION,
                [{"id": "service_health", "shown": shown}],
            )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail="Observatory durable state is unavailable"
        ) from exc
    return get_mission_service_visibility()


@router.get("/mission/overview/summary")
def get_mission_overview_summary(
    store: RunEvidenceStore = Depends(get_run_evidence_store),
) -> ReadEnvelope[list[SummaryMetricRow]]:
    """Return the Overview summary band, derived from live execution state."""
    return serve_sourced(
        lambda: derive_overview_metrics(store), "overview_summary", SummaryMetricRow
    )
