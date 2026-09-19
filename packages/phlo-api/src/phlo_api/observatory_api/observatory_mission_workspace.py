"""Workspace settings read models: provider connections, notifications, members
and defaults.

Credentials are never returned. ``ProviderConnection.credential_ref`` is an
opaque locator resolved server-side, and ``endpoint`` is a provider-neutral
display string; neither may contain a scheme that reaches a browser directly.
"""

from __future__ import annotations

from fastapi import APIRouter

from phlo_api.observatory_api.observatory_mission_control_models import (
    ReadEnvelope,
    NotificationRule,
    SummaryMetricRow,
    ProviderConnection,
    ProviderImpact,
    WorkspaceDefault,
    WorkspaceMember,
)
from phlo_api.observatory_api.observatory_mission_control_state import (
    serve_collection,
    serve_record,
)

router = APIRouter()


@router.get("/mission/settings/providers")
def get_mission_provider_connections() -> ReadEnvelope[list[ProviderConnection]]:
    """Return provider connections and their reachability state."""
    return serve_collection("provider_connections", ProviderConnection)


@router.get("/mission/settings/providers/{provider_id}/impact")
def get_mission_provider_impact(provider_id: str) -> ReadEnvelope[ProviderImpact]:
    """Return what a degraded provider does and does not affect."""
    return serve_record(
        "provider_impact",
        ProviderImpact,
        f"No impact summary for {provider_id}",
        id=provider_id,
    )


@router.get("/mission/settings/notifications")
def get_mission_notification_rules() -> ReadEnvelope[list[NotificationRule]]:
    """Return workspace notification routing rules."""
    return serve_collection("notification_rules", NotificationRule)


@router.get("/mission/settings/members")
def get_mission_workspace_members() -> ReadEnvelope[list[WorkspaceMember]]:
    """Return members of the workspace."""
    return serve_collection("workspace_members", WorkspaceMember)


@router.get("/mission/settings/defaults")
def get_mission_workspace_defaults() -> ReadEnvelope[list[WorkspaceDefault]]:
    """Return workspace-wide default settings."""
    return serve_collection("workspace_defaults", WorkspaceDefault)


@router.get("/mission/settings/summary")
def get_mission_settings_summary() -> ReadEnvelope[list[SummaryMetricRow]]:
    """Return the Settings summary band."""
    return serve_collection("settings_summary", SummaryMetricRow)
