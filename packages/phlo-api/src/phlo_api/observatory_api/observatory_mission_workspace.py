"""Workspace settings read models: provider connections, notifications, members
and defaults.

Credentials are never returned. ``ProviderConnection.credential_ref`` is an
opaque locator resolved server-side, and ``endpoint`` is a provider-neutral
display string; neither may contain a scheme that reaches a browser directly.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from phlo_api.observatory_api.observatory_mission_control_models import (
    NotificationRule,
    SummaryMetricRow,
    ProviderConnection,
    ProviderImpact,
    WorkspaceDefault,
    WorkspaceMember,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    find_record,
    load_records,
)

router = APIRouter()


@router.get("/mission/settings/providers")
def get_mission_provider_connections() -> list[ProviderConnection]:
    """Return provider connections and their reachability state."""
    return load_records("provider_connections", ProviderConnection)


@router.get("/mission/settings/providers/{provider_id}/impact")
def get_mission_provider_impact(provider_id: str) -> ProviderImpact:
    """Return what a degraded provider does and does not affect."""
    record = find_record("provider_impact", ProviderImpact, provider_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No impact summary for {provider_id}")
    return record


@router.get("/mission/settings/notifications")
def get_mission_notification_rules() -> list[NotificationRule]:
    """Return workspace notification routing rules."""
    return load_records("notification_rules", NotificationRule)


@router.get("/mission/settings/members")
def get_mission_workspace_members() -> list[WorkspaceMember]:
    """Return members of the workspace."""
    return load_records("workspace_members", WorkspaceMember)


@router.get("/mission/settings/defaults")
def get_mission_workspace_defaults() -> list[WorkspaceDefault]:
    """Return workspace-wide default settings."""
    return load_records("workspace_defaults", WorkspaceDefault)


@router.get("/mission/settings/summary")
def get_mission_settings_summary() -> list[SummaryMetricRow]:
    """Return the Settings summary band."""
    return load_records("settings_summary", SummaryMetricRow)
