"""Core hook sink that records correlated lifecycle events.

CoreRunEvidenceHookProvider translates lifecycle hook events into rows
in the run-evidence store. Persistence is observational: failures are
logged, never raised, and events without a complete (project_id,
run_id) correlation are intentionally skipped rather than persisted
under partial keys.
Imported by the phlo hooks bus and the phlo.run_evidence package as the core sink that records
lifecycle events through phlo.plugins.hooks.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from phlo.capabilities import ResourceRef
from phlo.capabilities.specs import is_blocking_severity
from phlo.hooks.events import (
    HookEvent,
    IngestionEvent,
    LineageEvent,
    PublishEvent,
    QualityResultEvent,
    RunEvidenceObservationEvent,
    TransformEvent,
)
from phlo.plugins.hooks import FailurePolicy, HookFilter, HookRegistration
from phlo.run_evidence.models import (
    PipelineRun,
    RunArtifact,
    RunCatalogChange,
    RunEvent,
    RunLineageEdge,
    RunQualityResult,
    RunResource,
    RunStage,
)
from phlo.run_evidence.store import (
    _SqlRunEvidenceStore,
    default_run_evidence_store,
)

_LIFECYCLE_EVENT_TYPES = {
    "run.start",
    "run.end",
    "run.heartbeat",
    "ingestion.start",
    "ingestion.end",
    "transform.start",
    "transform.end",
    "quality.result",
    "publish.start",
    "publish.end",
    "lineage.edges",
    "run_evidence.observation",
}


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:32]


class CoreRunEvidenceHookProvider:
    """Persist the provider-neutral lifecycle events already emitted by Phlo."""

    def __init__(self, store: _SqlRunEvidenceStore | None = None) -> None:
        self._store = store

    def get_hooks(self) -> list[HookRegistration]:
        """Register the lifecycle-event hook that persists run evidence with log-only failures."""
        return [
            HookRegistration(
                hook_name="core_run_evidence",
                handler=self._handle_event,
                priority=0,
                filters=HookFilter(event_types=set(_LIFECYCLE_EVENT_TYPES)),
                # Evidence persistence is observational. A provider operation
                # must not become a provider failure because its post-submit
                # evidence sink is unavailable.
                failure_policy=FailurePolicy.LOG,
            )
        ]

    def _handle_event(self, event: HookEvent) -> None:
        project_id, run_id = _event_correlation(event)
        if run_id is None:
            # Legacy hook callers can emit uncorrelated events. They cannot be
            # safely attached to a durable run and are intentionally skipped.
            return
        if project_id is None:
            # A run ID without its project is an incomplete correlation, not a
            # tenant identity. Keep legacy hook callers operational while
            # refusing to persist evidence that cannot satisfy the composite
            # tenant keys in the store.
            return

        store = self._store or default_run_evidence_store()
        self._store = store
        run_status = _run_status(event)
        terminal_statuses = {
            "success",
            "failed",
            "error",
            "cancelled",
            "canceled",
            "skipped",
            "no_data",
            "abandoned",
        }
        run = PipelineRun(
            project_id=project_id,
            run_id=run_id,
            pipeline_name=event.correlation.job_name,
            provider_run_id=run_id,
            partition_key=event.correlation.partition_key,
            trace_id=event.correlation.trace_id,
            status=run_status,
            attempt=event.correlation.attempt,
            started_at=event.timestamp if event.event_type == "run.start" else None,
            finished_at=event.timestamp if run_status in terminal_statuses else None,
            failure_summary=getattr(event, "error", None),
        )
        stage = _stage_for_event(event, project_id=project_id, run_id=run_id)
        quality_result = _quality_for_event(
            event, project_id=project_id, run_id=run_id, stage=stage
        )
        lineage_edges = _lineage_for_event(event, project_id=project_id, run_id=run_id)
        resources = _resources_for_event(event, project_id=project_id, run_id=run_id)
        catalog_change = _catalog_change_for_event(event, project_id=project_id, run_id=run_id)
        artifacts = _artifacts_for_event(event, project_id=project_id, run_id=run_id)
        store.append_event(
            RunEvent(
                project_id=project_id,
                run_id=run_id,
                event_id=event.event_id,
                event_type=event.event_type,
                producer=event.producer,
                schema_version=event.version,
                observed_at=event.timestamp,
                payload=_event_payload(event),
                attempt=event.correlation.attempt,
                stage_id=stage.stage_id if stage else None,
                resource_ref=stage.resource_ref
                if stage is not None
                else ResourceRef(resource_type="run", resource_id=run_id, tenant=project_id),
            ),
            run=run,
            stage=stage,
            quality_result=quality_result,
            lineage_edges=tuple(lineage_edges),
            resources=tuple(resources),
            catalog_change=catalog_change,
            artifacts=tuple(artifacts),
        )


def _event_correlation(event: HookEvent) -> tuple[str | None, str | None]:
    project_id = event.correlation.project_id
    run_id = event.correlation.run_id or getattr(event, "run_id", None)
    event_run_id = getattr(event, "run_id", None)
    if event_run_id is not None and event.correlation.run_id not in (None, event_run_id):
        raise ValueError("event run_id and correlation.run_id do not match")
    return project_id, run_id


def _event_payload(event: HookEvent) -> dict[str, Any]:
    """Keep envelope identity/time in columns so retries hash logical content."""
    payload = asdict(event)
    payload.pop("event_id", None)
    payload.pop("timestamp", None)
    correlation = payload.get("correlation")
    if isinstance(correlation, dict):
        payload["correlation"] = {
            key: correlation.get(key)
            for key in (
                "project_id",
                "run_id",
                "attempt",
                "asset_key",
                "job_name",
                "partition_key",
                "check_name",
            )
        }
    return payload


def _stage_for_event(event: HookEvent, *, project_id: str, run_id: str) -> RunStage | None:
    stage_type = _stage_type(event)
    if stage_type is None:
        return None
    asset = getattr(event, "asset_key", None)
    check_name = getattr(event, "check_name", None)
    stage_key = check_name or asset or getattr(event.correlation, "job_name", None) or stage_type
    stage_id = _stable_id(
        project_id, run_id, str(event.correlation.attempt), stage_type, str(stage_key)
    )
    status = getattr(event, "status", None) or "observed"
    if status in {"started", "start", "running"}:
        status = "running"
    elif status == "rejected":
        status = "failed"
    stage_started_at = (
        event.timestamp if event.event_type.endswith(".start") or status == "running" else None
    )
    stage_finished_at = event.timestamp if status not in {"running", "observed"} else None
    return RunStage(
        project_id=project_id,
        run_id=run_id,
        stage_id=stage_id,
        stage_type=stage_type,
        provider="hook",
        tool=getattr(event, "tool", None) or getattr(event, "target_system", None),
        asset=asset,
        status=status,
        attempt=event.correlation.attempt,
        started_at=stage_started_at,
        finished_at=stage_finished_at,
        metrics=getattr(event, "metrics", {}) or {},
        error=getattr(event, "error", None),
        resource_ref=ResourceRef(
            resource_type="asset" if asset else "stage",
            resource_id=asset or stage_id,
            tenant=project_id,
        ),
    )


def _quality_for_event(
    event: HookEvent,
    *,
    project_id: str,
    run_id: str,
    stage: RunStage | None,
) -> RunQualityResult | None:
    if not isinstance(event, QualityResultEvent):
        return None
    metadata = event.metadata or {}
    return RunQualityResult(
        project_id=project_id,
        run_id=run_id,
        quality_result_id=_stable_id(
            project_id, run_id, str(event.correlation.attempt), "quality", event.check_name
        ),
        check_id=event.check_name,
        attempt=event.correlation.attempt,
        asset=event.asset_key,
        stage_id=stage.stage_id if stage else None,
        severity=event.severity,
        blocking=is_blocking_severity(event.severity),
        passed=event.passed,
        evaluated_count=_as_int(metadata.get("evaluated_count", metadata.get("total_rows"))),
        failed_count=_as_int(metadata.get("failed_count", metadata.get("failed_rows"))),
        metadata=metadata,
        resource_ref=ResourceRef(
            resource_type="quality_check", resource_id=event.check_name, tenant=project_id
        ),
    )


def _lineage_for_event(
    event: HookEvent,
    *,
    project_id: str,
    run_id: str,
) -> list[RunLineageEdge]:
    if not isinstance(event, LineageEvent):
        return []
    metadata = event.metadata or {}
    return [
        RunLineageEdge(
            project_id=project_id,
            run_id=run_id,
            lineage_edge_id=_stable_id(
                project_id, run_id, str(event.correlation.attempt), event.event_id, str(index)
            ),
            attempt=event.correlation.attempt,
            source=source,
            target=target,
            column_mapping=metadata.get("column_mapping", {}),
            origin=str(metadata.get("origin", "observed")),
            derivation=str(metadata.get("derivation", "exact")),
            confidence=_as_float(metadata.get("confidence")),
            source_resource_ref=_asset_resource_ref(source, project_id),
            target_resource_ref=_asset_resource_ref(target, project_id),
        )
        for index, (source, target) in enumerate(event.edges)
    ]


def _stage_type(event: HookEvent) -> str | None:
    if isinstance(event, RunEvidenceObservationEvent):
        return event.observation_type
    if isinstance(event, IngestionEvent):
        return "ingest"
    if isinstance(event, TransformEvent):
        return "transform"
    if isinstance(event, QualityResultEvent):
        return "check"
    if isinstance(event, PublishEvent):
        return "publish"
    if isinstance(event, LineageEvent):
        return "lineage"
    return None


def _resources_for_event(event: HookEvent, *, project_id: str, run_id: str) -> list[Any]:
    if not isinstance(event, RunEvidenceObservationEvent):
        return []
    resources: list[Any] = []
    for index, raw in enumerate(event.resources):
        if not isinstance(raw, dict):
            continue
        values = dict(raw)
        values.setdefault(
            "resource_id",
            _stable_id(
                project_id, run_id, str(event.correlation.attempt), event.event_id, str(index)
            ),
        )
        values["project_id"] = project_id
        values["run_id"] = run_id
        values.setdefault("attempt", event.correlation.attempt)
        values["resource_ref"] = _resource_ref_from_mapping(
            values.get("resource_identity"), project_id
        )
        if values["resource_ref"] is None:
            raise ValueError(
                "observation resource requires canonical project-scoped resource_identity"
            )
        allowed = {
            "project_id",
            "run_id",
            "resource_id",
            "attempt",
            "resource_kind",
            "role",
            "normalized_identity",
            "uri",
            "table_name",
            "catalog",
            "ref_name",
            "schema_hash",
            "schema_hash_before",
            "schema_hash_after",
            "watermark",
            "record_count",
            "byte_count",
            "staged_objects",
            "snapshot_before",
            "snapshot_after",
            "metadata",
            "resource_ref",
        }
        resources.append(
            RunResource(**{key: value for key, value in values.items() if key in allowed})
        )
    return resources


def _catalog_change_for_event(
    event: HookEvent, *, project_id: str, run_id: str
) -> RunCatalogChange | None:
    if not isinstance(event, RunEvidenceObservationEvent) or not event.catalog_change:
        return None
    values = dict(event.catalog_change)
    values.setdefault(
        "catalog_change_id",
        _stable_id(project_id, run_id, str(event.correlation.attempt), event.event_id, "catalog"),
    )
    values.update(project_id=project_id, run_id=run_id)
    values.setdefault("attempt", event.correlation.attempt)
    values["resource_ref"] = _resource_ref_from_mapping(values.get("resource_identity"), project_id)
    if values["resource_ref"] is None:
        raise ValueError("catalog change requires canonical project-scoped resource_identity")
    allowed = {
        "project_id",
        "run_id",
        "catalog_change_id",
        "attempt",
        "operation",
        "catalog_ref",
        "content_key",
        "source_hash",
        "target_hash",
        "commit_hash",
        "commit_message",
        "merge_outcome",
        "snapshot_before",
        "snapshot_after",
        "quality_decision_id",
        "metadata",
        "resource_ref",
    }
    return RunCatalogChange(**{key: value for key, value in values.items() if key in allowed})


def _artifacts_for_event(event: HookEvent, *, project_id: str, run_id: str) -> list[RunArtifact]:
    if not isinstance(event, RunEvidenceObservationEvent):
        return []
    artifacts: list[RunArtifact] = []
    for raw in event.artifacts:
        if not isinstance(raw, dict) or not isinstance(raw.get("artifact_id"), str):
            continue
        values = dict(raw)
        values.update(project_id=project_id, run_id=run_id)
        values.setdefault("attempt", event.correlation.attempt)
        values["resource_ref"] = _resource_ref_from_mapping(
            values.get("resource_identity"), project_id
        )
        if values["resource_ref"] is None:
            raise ValueError("artifact requires canonical project-scoped resource_identity")
        allowed = {
            "project_id",
            "run_id",
            "artifact_id",
            "artifact_kind",
            "uri",
            "content_type",
            "checksum",
            "retention_class",
            "expires_at",
            "legal_hold",
            "status",
            "attempt",
            "resource_ref",
        }
        artifacts.append(
            RunArtifact(**{key: value for key, value in values.items() if key in allowed})
        )
    return artifacts


def _resource_ref_from_mapping(value: Any, project_id: str) -> ResourceRef | None:
    if not isinstance(value, dict):
        return None
    resource_type = value.get("resource_type")
    resource_id = value.get("resource_id")
    attributes = value.get("attributes", {})
    if (
        not isinstance(resource_type, str)
        or not resource_type.strip()
        or not isinstance(resource_id, str)
        or not resource_id.strip()
        or value.get("tenant") != project_id
        or not isinstance(attributes, dict)
        or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in attributes.items()
        )
    ):
        return None
    return ResourceRef(resource_type, resource_id, tenant=project_id, attributes=attributes)


def _asset_resource_ref(value: str, project_id: str) -> ResourceRef | None:
    return (
        ResourceRef(resource_type="asset", resource_id=value, tenant=project_id)
        if value.strip()
        else None
    )


def _run_status(event: HookEvent) -> str:
    if isinstance(event, RunEvidenceObservationEvent):
        if event.run_status in {
            "queued",
            "not_started",
            "starting",
            "started",
            "running",
            "canceling",
            "success",
            "failed",
            "error",
            "cancelled",
            "canceled",
            "skipped",
            "no_data",
            "abandoned",
        }:
            return event.run_status
        if event.status in {"failed", "error", "cancelled", "canceled", "no_data"}:
            return event.status
        return "running"
    if event.event_type not in {"run.end", "run.terminal"}:
        return "running"
    status = getattr(event, "status", None)
    if status in {"success", "failed", "error", "cancelled", "canceled", "skipped"}:
        return str(status)
    return "running"


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
