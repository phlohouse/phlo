"""Mission Control shell and Overview read models.

Serves the alerts inbox, environment list, attention queue and active-execution
table. These are operator-facing read models layered over the platform, so they
are namespaced under ``/mission`` to keep them distinct from the substrate
endpoints in ``observatory.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from phlo_api.observatory_api.observatory_mission_control_models import (
    MissionAlert,
    MissionAttentionItem,
    MissionEnvironment,
    MissionExecutionRow,
)
from phlo_api.observatory_api.observatory_mission_control_state import load_records

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
