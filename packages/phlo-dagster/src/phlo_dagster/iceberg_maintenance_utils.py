"""Shared utilities for Iceberg table maintenance operations.

This module provides common helpers used by Iceberg maintenance jobs
and sensors. It includes configuration models, telemetry tagging, logging
utilities, and catalog interaction functions.

Configuration:
    MaintenanceConfig: Pydantic model for maintenance parameters:
    - namespace: Target namespace or "all"
    - snapshot_retention_days: Age threshold for snapshot expiration
    - snapshot_retain_last: Minimum snapshots to preserve
    - orphan_retention_days: Age threshold for orphan file deletion
    - dry_run: Plan-only mode; snapshot execution requires an explicit plan token and executor
    - catalog, confirmation_token, confirmation_tokens: Plan-binding fields for guarded snapshot expiry
    - max_affected_objects, max_affected_bytes: Finite limits revalidated before provider submission
    - ref: Nessie branch reference (default: main)
    - table_allowlist: Optional restriction to specific tables

Telemetry Support:
    - maintenance_tags(): Build telemetry context tags
    - maintenance_payload(): Construct structured event payloads
    - maintenance_log_extra(): Prepare logging extra fields
    - start_maintenance_op(): Emit start telemetry and logs
    - finish_maintenance_op(): Emit completion telemetry and metrics
    - emit_maintenance_metrics(): Publish standard metrics

Catalog Operations:
    - list_tables(): Get fully qualified table names in a namespace
    - list_namespaces(): Get all namespaces for a reference
    - resolve_namespaces(): Expand "all" or return specific namespace

Integration Requirements:
    Requires a registered ``table_store:iceberg`` provider implementing the
    neutral maintenance discovery contract. Functions resolve that capability
    at runtime without importing a concrete provider package.

Example:
    Configuration and telemetry::

        from phlo_dagster.iceberg_maintenance_utils import (
            MaintenanceConfig,
            start_maintenance_op,
            finish_maintenance_op,
        )

        config = MaintenanceConfig(
            namespace="raw",
            snapshot_retention_days=7,
            ref="main",
        )

        telemetry = start_maintenance_op(context, config, "expire_snapshots")
        # ... perform maintenance ...
        summary = finish_maintenance_op(
            context, config, telemetry, "expire_snapshots",
            duration_seconds=elapsed, errors=errors,
            tables_processed=10, snapshots_deleted=50,
        )

"""

from __future__ import annotations

from typing import Annotated, Any, Optional

import dagster as dg
from phlo.capabilities import MaintenanceDiscovery, resolve_capability
from phlo.hooks import HookCorrelation, TelemetryEventContext, TelemetryEventEmitter
from pydantic import Field

from phlo.logging import get_logger

logger = get_logger(__name__)


def resolve_maintenance_discovery() -> MaintenanceDiscovery:
    """Resolve neutral discovery and statistics capabilities for maintenance."""
    resolution = resolve_capability("table_store", "iceberg")
    if resolution is None:
        raise RuntimeError("Maintenance discovery requires a table_store:iceberg capability.")
    provider = resolution.provider
    if not isinstance(provider, MaintenanceDiscovery):
        raise RuntimeError(
            "Resolved table_store:iceberg does not implement the maintenance discovery contract."
        )
    return provider


class MaintenanceConfig(dg.Config):
    """Configuration for Iceberg table maintenance operations.

    Namespace ``"all"`` targets every namespace on ``ref``. With ``dry_run``
    enabled a plan is produced only; executing it requires the confirmation
    token(s) returned by the plan, and orphan cleanup is refused.
    """

    # Namespace to run maintenance on (or 'all' for all namespaces)
    namespace: str = "raw"
    # Expire snapshots older than this many days (must be positive)
    snapshot_retention_days: Annotated[int, Field(gt=0)] = 7
    # Always retain at least this many snapshots
    snapshot_retain_last: Annotated[int, Field(ge=1)] = 5
    # Only remove orphan files older than this many days (cannot be less than 7)
    orphan_retention_days: Annotated[int, Field(ge=7)] = 7
    # Deprecated compatibility flag; dry_run is authoritative.
    orphan_dry_run: Optional[bool] = None
    # Nessie branch reference
    ref: str = "main"
    # Optional allowlist of fully qualified table names to restrict maintenance to
    table_allowlist: Optional[list[str]] = None
    dry_run: bool = True
    catalog: Optional[str] = None
    confirmation_token: Optional[str] = None
    confirmation_tokens: Optional[dict[str, str]] = None
    max_affected_objects: Annotated[int, Field(ge=0)] = 1000
    max_affected_bytes: Annotated[int, Field(ge=0)] = 10 * 1024 * 1024 * 1024


def maintenance_tags(
    config: MaintenanceConfig,
    *,
    operation: str,
    dry_run: bool | None = None,
    status: str | None = None,
) -> dict[str, str]:
    """Build telemetry tag values for a maintenance operation.

    Includes the operation name, namespace, and ref, plus optional dry-run
    and status labels.
    """

    tags = {
        "maintenance": "true",
        "operation": operation,
        "namespace": config.namespace,
        "ref": config.ref,
    }
    if dry_run is not None:
        tags["dry_run"] = str(dry_run).lower()
    if status:
        tags["status"] = status
    return tags


def maintenance_payload(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
    *,
    operation: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a structured telemetry payload for a maintenance operation."""

    payload = {
        "operation": operation,
        "namespace": config.namespace,
        "ref": config.ref,
        "run_id": context.run_id,
        "job_name": context.job_name,
    }
    payload.update(extra)
    return payload


def maintenance_log_extra(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
    *,
    operation: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build structured ``extra`` fields for maintenance log records."""

    return {
        "maintenance_op": operation,
        "namespace": config.namespace,
        "ref": config.ref,
        "run_id": context.run_id,
        "job_name": context.job_name,
        **extra,
    }


def emit_maintenance_metrics(
    emitter: TelemetryEventEmitter,
    *,
    duration_seconds: float,
    tables_processed: int,
    errors: int,
    snapshots_deleted: int | None = None,
    total_candidate_snapshots: int | None = None,
    orphan_files: int | None = None,
    candidate_orphan_files: int | None = None,
    deleted_orphan_files: int | None = None,
    unavailable_deleted_file_evidence: int | None = None,
    total_records: int | None = None,
    total_size_mb: float | None = None,
) -> None:
    """Emit standard maintenance run metrics."""

    payload = dict(emitter._context.tags)
    emitter.emit_metric(name="iceberg.maintenance.run", value=1, unit="run", payload=payload)
    emitter.emit_metric(
        name="iceberg.maintenance.duration_seconds",
        value=duration_seconds,
        unit="seconds",
        payload=payload,
    )
    emitter.emit_metric(
        name="iceberg.maintenance.tables_processed",
        value=tables_processed,
        unit="tables",
        payload=payload,
    )
    emitter.emit_metric(
        name="iceberg.maintenance.errors",
        value=errors,
        unit="errors",
        payload=payload,
    )
    if snapshots_deleted is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.snapshots_deleted",
            value=snapshots_deleted,
            unit="snapshots",
            payload=payload,
        )
    if orphan_files is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.orphan_files",
            value=orphan_files,
            unit="files",
            payload=payload,
        )
    if candidate_orphan_files is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.candidate_orphan_files",
            value=candidate_orphan_files,
            unit="files",
            payload=payload,
        )
    if deleted_orphan_files is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.deleted_orphan_files",
            value=deleted_orphan_files,
            unit="files",
            payload=payload,
        )
    if unavailable_deleted_file_evidence is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.unavailable_deleted_file_evidence",
            value=unavailable_deleted_file_evidence,
            unit="count",
            payload=payload,
        )
    if total_records is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.total_records",
            value=total_records,
            unit="records",
            payload=payload,
        )
    if total_size_mb is not None:
        emitter.emit_metric(
            name="iceberg.maintenance.total_size_mb",
            value=total_size_mb,
            unit="mb",
            payload=payload,
        )


def resolve_namespaces(config: MaintenanceConfig) -> list[str]:
    """Resolve configured namespace scope into a namespace list.

    Expands the scope ``"all"`` by listing namespaces on the configured ref.
    """

    if config.namespace == "all":
        return list_namespaces(config.ref)
    return [config.namespace]


def start_maintenance_op(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
    operation: str,
    **extra_tags: Any,
) -> TelemetryEventEmitter:
    """Emit start telemetry and logs for a maintenance operation.

    Return the telemetry emitter built from the maintenance tags.
    """

    telemetry = TelemetryEventEmitter(
        TelemetryEventContext(
            tags=maintenance_tags(config, operation=operation, **extra_tags),
            correlation=HookCorrelation(run_id=context.run_id, job_name=context.job_name),
        )
    )
    context.log.info(
        "Starting Iceberg maintenance operation",
        extra=maintenance_log_extra(
            context, config, operation=operation, phase="start", **extra_tags
        ),
    )
    telemetry.emit_log(
        name="iceberg.maintenance.start",
        level="info",
        payload=maintenance_payload(context, config, operation=operation, **extra_tags),
    )
    return telemetry


def finish_maintenance_op(
    context: dg.OpExecutionContext,
    config: MaintenanceConfig,
    telemetry: TelemetryEventEmitter,
    operation: str,
    *,
    duration_seconds: float,
    errors: list[str],
    extra_tags: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    **metrics_kwargs: Any,
) -> dict[str, Any]:
    """Emit completion telemetry, logs, and metrics for maintenance.

    Status is ``success`` unless ``errors`` is non-empty; any supplied
    ``evidence`` is attached to the summary payload, which is returned.
    """

    tag_extras = extra_tags or {}
    status = "success" if not errors else "failure"
    summary_payload = maintenance_payload(
        context,
        config,
        operation=operation,
        status=status,
        duration_seconds=duration_seconds,
        errors=len(errors),
        **tag_extras,
        **metrics_kwargs,
    )
    if evidence:
        summary_payload["evidence"] = evidence
    context.log.info(
        "Completed Iceberg maintenance operation",
        extra=maintenance_log_extra(
            context,
            config,
            operation=operation,
            status=status,
            duration_seconds=duration_seconds,
            errors=len(errors),
            **tag_extras,
            **metrics_kwargs,
        ),
    )
    telemetry.emit_log(
        name="iceberg.maintenance.complete",
        level="info",
        payload=summary_payload,
    )
    if errors:
        telemetry.emit_log(
            name="iceberg.maintenance.failed",
            level="error",
            payload=summary_payload,
        )
    metrics_emitter = TelemetryEventEmitter(
        TelemetryEventContext(
            tags=maintenance_tags(config, operation=operation, status=status, **tag_extras),
            correlation=HookCorrelation(run_id=context.run_id, job_name=context.job_name),
        )
    )
    emit_maintenance_metrics(
        metrics_emitter,
        duration_seconds=duration_seconds,
        errors=len(errors),
        **metrics_kwargs,
    )
    return summary_payload


def list_tables(namespace: str, ref: str) -> list[str]:
    """List fully qualified table names in a namespace.

    Log failures and return an empty list rather than raising.
    """
    try:
        return resolve_maintenance_discovery().list_tables(namespace=namespace, ref=ref)
    except Exception:
        logger.exception("list_tables_failed", namespace=namespace)
        return []


def list_namespaces(ref: str) -> list[str]:
    """List catalog namespaces for a Nessie reference.

    Log failures and return an empty list rather than raising.
    """
    try:
        return resolve_maintenance_discovery().list_namespaces(ref=ref)
    except Exception:
        logger.exception("Failed to list namespaces")
        return []
