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

from typing import Any

from anyio.to_thread import run_sync
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from phlo.logging import get_logger
from phlo.plugins.observatory_settings import SettingsScope, get_settings_service
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
