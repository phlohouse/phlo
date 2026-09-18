"""Mission Control shell and Overview read models.

Serves the alerts inbox, environment list, attention queue and active-execution
table. These are operator-facing read models layered over the platform, so they
are namespaced under ``/mission`` to keep them distinct from the substrate
endpoints in ``observatory.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
    MissionAlert,
    MissionAttentionItem,
    MissionDataProduct,
    MissionEnvironment,
    MissionExecutionRow,
    MissionOverviewRail,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    find_record,
    load_records,
)

router = APIRouter()


@router.get("/mission/overview/alerts")
def get_mission_alerts() -> list[MissionAlert]:
    """Return the workspace alerts inbox, newest first."""
    return load_records("alerts", MissionAlert)


@router.get("/mission/overview/environments")
def get_mission_environments() -> list[MissionEnvironment]:
    """Return the environments the workspace can be scoped to."""
    return load_records("environments", MissionEnvironment)


@router.get("/mission/overview/attention")
def get_mission_attention() -> list[MissionAttentionItem]:
    """Return priority items ranked by consumer impact."""
    return load_records("attention", MissionAttentionItem)


@router.get("/mission/overview/execution")
def get_mission_execution() -> list[MissionExecutionRow]:
    """Return workflows currently running or queued."""
    return load_records("execution", MissionExecutionRow)


@router.get("/mission/overview/data-products")
def get_mission_data_products() -> list[MissionDataProduct]:
    """Return released datasets with freshness and quality signals."""
    return load_records("data_products", MissionDataProduct)


@router.get("/mission/overview/rail")
def get_mission_overview_rail() -> MissionOverviewRail:
    """Return the Overview right-hand rail as a single read model."""
    rail = find_record("overview_rail", MissionOverviewRail, "rail")
    if rail is None:
        raise HTTPException(status_code=404, detail="No overview rail configured")
    return rail
