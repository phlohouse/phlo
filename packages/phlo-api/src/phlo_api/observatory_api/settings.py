"""Server-wide Observatory settings endpoints.

Provides CRUD operations for global Observatory configuration.
Settings are persisted via the settings service and validated
against a strict JSON schema to ensure UI compatibility.

Key Endpoints:
    GET /api/observatory/settings: Get global Observatory settings.
    PUT /api/observatory/settings: Update global Observatory settings.

Example:
    Getting settings:

    .. code-block:: bash

        curl http://localhost:4000/api/observatory/settings

    Response includes connections, defaults, query, and UI configuration.

Authorization is enforced when an authorization backend is configured.
In strict mode, these endpoints fail closed if the backend is absent.


Serves the Observatory API settings surface: builds on phlo.plugins.observatory_settings
and the phlo-api authorization layer.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from anyio.to_thread import run_sync
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import yaml

from phlo.logging import get_logger
from phlo.capabilities import AlertSink, resolve_capability
from phlo.plugins.observatory_settings import (
    SettingsScope,
    get_settings_service,
    get_operational_settings,
    operational_environment_target,
    operational_schedule_slot,
)
from phlo_api.api.authorization import check_admin_manage, check_admin_read


logger = get_logger(__name__)

router = APIRouter(prefix="/api/observatory", tags=["observatory"])


class ObservatorySettingsPayload(BaseModel):
    """Request payload for updating Observatory settings."""

    settings: dict[str, Any]


class ObservatorySettingsResponse(BaseModel):
    """Response payload for Observatory settings endpoints."""

    settings: dict[str, Any] | None
    updated_at: str | None


OBSERVATORY_SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "connections", "defaults", "query", "ui"],
    "properties": {
        "version": {"type": "integer", "enum": [1]},
        "connections": {
            "type": "object",
            "additionalProperties": False,
            "required": ["dagsterGraphqlUrl", "trinoUrl", "nessieUrl"],
            "properties": {
                "dagsterGraphqlUrl": {"type": "string", "minLength": 1},
                "trinoUrl": {"type": "string", "minLength": 1},
                "nessieUrl": {"type": "string", "minLength": 1},
            },
        },
        "defaults": {
            "type": "object",
            "additionalProperties": False,
            "required": ["branch", "catalog", "schema"],
            "properties": {
                "branch": {"type": "string", "minLength": 1},
                "catalog": {"type": "string", "minLength": 1},
                "schema": {"type": "string", "minLength": 1},
            },
        },
        "query": {
            "type": "object",
            "additionalProperties": False,
            "required": ["readOnlyMode", "defaultLimit", "maxLimit", "timeoutMs"],
            "properties": {
                "readOnlyMode": {"type": "boolean"},
                "defaultLimit": {"type": "integer", "minimum": 1, "maximum": 100000},
                "maxLimit": {"type": "integer", "minimum": 1, "maximum": 100000},
                "timeoutMs": {"type": "integer", "minimum": 1000, "maximum": 300000},
            },
        },
        "ui": {
            "type": "object",
            "additionalProperties": False,
            "required": ["density", "dateFormat"],
            "properties": {
                "density": {"type": "string", "enum": ["comfortable", "compact"]},
                "dateFormat": {"type": "string", "enum": ["iso", "local"]},
            },
        },
        "auth": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"token": {"type": "string"}},
        },
        "realtime": {
            "type": "object",
            "additionalProperties": False,
            "required": ["enabled", "intervalMs"],
            "properties": {
                "enabled": {"type": "boolean"},
                "intervalMs": {"type": "integer", "minimum": 1000, "maximum": 60000},
            },
        },
    },
}

OBSERVATORY_SETTINGS_NAMESPACE = "observatory.core"


def _fetch_settings_sync() -> ObservatorySettingsResponse:
    """Fetch persisted global Observatory settings, or a null-settings
    response when none are stored.
    """
    service = get_settings_service()
    record = service.get(SettingsScope.GLOBAL, OBSERVATORY_SETTINGS_NAMESPACE)
    if not record:
        return ObservatorySettingsResponse(settings=None, updated_at=None)
    return ObservatorySettingsResponse(
        settings=record.settings,
        updated_at=record.updated_at,
    )


def _upsert_settings_sync(payload: ObservatorySettingsPayload) -> ObservatorySettingsResponse:
    """Persist global Observatory settings and return the saved record."""
    service = get_settings_service()
    record = service.put(
        SettingsScope.GLOBAL,
        OBSERVATORY_SETTINGS_NAMESPACE,
        payload.settings,
        schema=OBSERVATORY_SETTINGS_SCHEMA,
    )
    return ObservatorySettingsResponse(
        settings=record.settings,
        updated_at=record.updated_at,
    )


@router.get("/settings", response_model=ObservatorySettingsResponse)
async def get_observatory_settings(request: Request) -> ObservatorySettingsResponse:
    """Fetch server-wide Observatory settings. Raises HTTPException 503 when
    the settings service is unavailable, 500 on other errors.
    """
    check_admin_read(request, "observatory_settings")
    try:
        return await run_sync(_fetch_settings_sync)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to fetch Observatory settings")
        raise HTTPException(status_code=500, detail="Failed to fetch settings") from exc


@router.put("/settings", response_model=ObservatorySettingsResponse)
async def put_observatory_settings(
    request: Request,
    payload: ObservatorySettingsPayload,
) -> ObservatorySettingsResponse:
    """Replace server-wide Observatory settings. Raises HTTPException 503
    when the settings service is unavailable, 422 on validation failure,
    500 on other errors.
    """
    check_admin_manage(request, "observatory_settings")
    try:
        return await run_sync(_upsert_settings_sync, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update Observatory settings")
        raise HTTPException(status_code=500, detail="Failed to update settings") from exc


class NotificationUnavailableError(RuntimeError):
    """An operator-configured notification route is unavailable before sending."""


class NotificationDeliveryError(RuntimeError):
    """Delivery was not confirmed. A retry may duplicate an external delivery."""


def _notification_sink(name: str) -> AlertSink:
    resolution = resolve_capability("alert_sink", name)
    if resolution is None or not isinstance(resolution.provider, AlertSink):
        raise NotificationUnavailableError(f"Notification route {name} is not configured.")
    sink = resolution.provider
    validate_delivery = getattr(sink, "validate_delivery", None)
    if callable(validate_delivery):
        try:
            validate_delivery()
        except RuntimeError as exc:
            raise NotificationUnavailableError("Notification destination is unavailable.") from exc
    return sink


def preflight_incident_notification(*, notify_qa: bool) -> None:
    """Check routes without sending. Caller authorises the effect before enqueue."""
    _notification_sink("qa" if notify_qa else "alerting")


def get_operational_maintenance_windows(env: str) -> list[dict[str, str]] | None:
    """Describe upcoming UTC policy-sensor slots, never observed downtime.

    A durable preference alone is insufficient. The operator must bind this
    consumer to the environment and configure an allowlisted namespace policy.
    This read does not enable a sensor or authorise destructive execution.
    """
    if os.environ.get("PHLO_OBSERVATORY_ENVIRONMENT") != env:
        return None
    _location, ref = operational_environment_target(env)
    settings = get_operational_settings()
    path = Path(os.environ.get("PHLO_MAINTENANCE_POLICY_PATH", "maintenance_policy.yaml"))
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        policies = payload.get("policies")
        if not isinstance(policies, list) or not any(
            isinstance(policy, dict)
            and policy.get("ref", "main") == ref
            and policy.get("namespace")
            for policy in policies
        ):
            return None
    except (OSError, yaml.YAMLError):
        return None
    schedules = []
    if settings.compact_nightly or settings.expire_snapshots_days is not None:
        schedules.append(
            (
                "maintenance",
                "Daily at 02:00",
                "Table maintenance policy evaluation; requires running Dagster sensor",
            )
        )
    if settings.orphan_cleanup:
        schedules.append(
            (
                "orphans",
                settings.orphan_cleanup,
                "Orphan discovery only; destructive orphan deletion is unavailable",
            )
        )
    now = datetime.now(UTC)
    items = []
    for offset in range(8):
        day = (now + timedelta(days=offset)).replace(hour=23, minute=59)
        for name, schedule, description in schedules:
            slot = operational_schedule_slot(schedule, day)
            if slot is not None and slot + timedelta(hours=1) > now:
                items.append(
                    {
                        "id": f"observatory:{env}:{name}:{slot.date()}:v{settings.settings_revision}",
                        "starts_at": slot.isoformat(),
                        "ends_at": (slot + timedelta(hours=1)).isoformat(),
                        "description": description,
                    }
                )
    return sorted(items, key=lambda item: item["starts_at"])


def send_incident_notification(
    request: Request | None,
    *,
    env: str,
    incident_id: str,
    effect_id: str,
    severity: str,
    owner: str | None,
    asset_ids: list[str],
    notify_qa: bool,
) -> bool:
    """Deliver an authorised outbox effect, with no free text or secret values.

    The incident owner checks service.manage before enqueue. Recovery does not
    need a browser session. The durable outbox owns retries; sink exceptions
    and False mean unconfirmed, never success. Effect identity is carried in
    run_id and error_message for provider deduplication, not exactly-once claims.
    QA uses an explicit qa capability, never the owner's route.
    """
    if env not in {"prod", "staging"} or severity not in {"low", "medium", "high", "critical"}:
        raise ValueError("Invalid notification environment or severity.")
    if not incident_id or not effect_id:
        raise ValueError("Notification requires incident and effect identity.")
    settings = get_operational_settings()
    names = ["qa" if notify_qa else "alerting"]
    if settings.notify_owners and owner:
        names.append(f"owner:{owner}")
    if settings.notify_consumers:
        names.extend(f"consumer:{asset_id}" for asset_id in sorted(set(asset_ids)))
    sinks = [_notification_sink(name) for name in dict.fromkeys(names)]
    message = json.dumps(
        {
            "env": env,
            "incident_id": incident_id,
            "effect_id": effect_id,
            "severity": severity,
            "owner": owner,
            "asset_ids": sorted(set(asset_ids)),
            "notify_qa": notify_qa,
        },
        separators=(",", ":"),
    )
    for sink in sinks:
        try:
            confirmed = sink.send_alert(
                title=f"Incident {incident_id}",
                message=message,
                severity={
                    "low": "INFO",
                    "medium": "WARNING",
                    "high": "ERROR",
                    "critical": "CRITICAL",
                }[severity],
                asset_name=",".join(sorted(set(asset_ids))) or None,
                run_id=f"{env}:{effect_id}",
                error_message=f"incident-effect:{env}:{effect_id}",
            )
        except Exception as exc:
            raise NotificationDeliveryError("Notification outcome is uncertain.") from exc
        if confirmed is not True:
            raise NotificationDeliveryError("Notification delivery was not confirmed.")
    return True
