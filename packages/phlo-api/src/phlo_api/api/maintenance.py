"""Maintenance API Router.

Endpoints for Iceberg maintenance observability data.

This module provides API endpoints for querying maintenance operation status
and metrics from the maintenance read-model capability. It enables monitoring
of data lifecycle operations like compaction, cleanup, and optimization.

Key Endpoints:
    GET /status: Get maintenance status snapshot.
    GET /metrics: Get Prometheus-formatted maintenance metrics.

Environment Variables:
    PHLO_MAINTENANCE_READ_MODEL: Name of the maintenance read model provider.

Example:
    Querying maintenance status:

    .. code-block:: bash

        curl http://localhost:4000/api/maintenance/status

    Response:

    .. code-block:: json

        {
            "last_updated": "2024-01-15T10:30:00",
            "operations": [
                {
                    "operation": "OPTIMIZE",
                    "namespace": "warehouse",
                    "ref": "main",
                    "status": "COMPLETED"
                }
            ]
        }

"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from phlo.capabilities import MaintenanceReadModel, list_capabilities, resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["maintenance"])
_DEFAULT_READ_MODEL_ENV = "PHLO_MAINTENANCE_READ_MODEL"


def _resolve_maintenance_read_model() -> MaintenanceReadModel:
    """Resolve the configured maintenance read-model capability."""
    discover_capabilities()
    name = os.environ.get(_DEFAULT_READ_MODEL_ENV)
    resolution = resolve_capability("maintenance_read_model", name)
    if resolution is None:
        available = list_capabilities("maintenance_read_model")
        if name:
            raise RuntimeError(
                f"Maintenance read model '{name}' not found. Available providers: {available}"
            )
        if available:
            raise RuntimeError(
                "Multiple maintenance_read_model providers are installed. "
                f"Set {_DEFAULT_READ_MODEL_ENV} to select one: {available}"
            )
        raise RuntimeError(
            "Maintenance observability requires a maintenance_read_model capability. "
            "Install the core maintenance provider or another provider."
        )
    return resolution.provider


class MaintenanceOperationStatus(BaseModel):
    """Serialized status for one maintenance operation run."""

    operation: str
    namespace: str
    ref: str
    status: str
    completed_at: str
    duration_seconds: float | None
    tables_processed: int
    errors: int
    snapshots_deleted: int
    orphan_files: int
    total_records: int
    total_size_mb: float
    dry_run: bool | None = None
    run_id: str | None = None
    job_name: str | None = None


class MaintenanceStatusSnapshot(BaseModel):
    """Top-level maintenance status payload."""

    last_updated: str
    operations: list[MaintenanceOperationStatus]


@router.get("/status", response_model=MaintenanceStatusSnapshot | dict)
def get_maintenance_status() -> MaintenanceStatusSnapshot | dict[str, str]:
    """Get maintenance status snapshot from the read model."""
    try:
        snapshot = _resolve_maintenance_read_model().load_maintenance_status()
        logger.debug("maintenance_status_loaded", operation_count=len(snapshot.operations))
        return _serialize_snapshot(snapshot)
    except Exception as exc:
        logger.exception("maintenance_status_load_failed")
        return {"error": str(exc)}


@router.get("/metrics", response_class=PlainTextResponse)
def get_maintenance_metrics() -> PlainTextResponse:
    """Expose maintenance metrics in Prometheus text format."""
    try:
        metrics_payload = _resolve_maintenance_read_model().render_maintenance_prometheus()
        logger.debug("maintenance_metrics_rendered", payload_length=len(metrics_payload))
        return PlainTextResponse(metrics_payload)
    except Exception as exc:
        logger.exception("maintenance_metrics_render_failed")
        return PlainTextResponse(f"# error: {exc}\n", status_code=500)


def _serialize_snapshot(snapshot: Any) -> MaintenanceStatusSnapshot:
    """Convert domain snapshot data into API response format."""
    return MaintenanceStatusSnapshot(
        last_updated=_isoformat(snapshot.last_updated),
        operations=[_serialize_operation(op) for op in snapshot.operations],
    )


def _serialize_operation(operation: Any) -> MaintenanceOperationStatus:
    """Convert a maintenance operation record into API response format."""
    return MaintenanceOperationStatus(
        operation=operation.operation,
        namespace=operation.namespace,
        ref=operation.ref,
        status=operation.status,
        completed_at=_isoformat(operation.completed_at),
        duration_seconds=operation.duration_seconds,
        tables_processed=operation.tables_processed,
        errors=operation.errors,
        snapshots_deleted=operation.snapshots_deleted,
        orphan_files=operation.orphan_files,
        total_records=operation.total_records,
        total_size_mb=operation.total_size_mb,
        dry_run=operation.dry_run,
        run_id=operation.run_id,
        job_name=operation.job_name,
    )


def _isoformat(value: datetime | Any) -> str:
    """Return an ISO timestamp when possible, else a string conversion."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
