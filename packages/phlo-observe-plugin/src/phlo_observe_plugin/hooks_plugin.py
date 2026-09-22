"""Hook plugin translating Phlo hook events into canonical phlo-observe events.

Phlo's hook bus is the internal event spine: ingestion, transform, quality,
publish, lineage, evidence-observation and lifecycle events already flow
through it with correlation merged in. This plugin re-emits them as
``phlo-observe`` wide events so a real run reconstructs in the observer.

Correlation model:

- ``correlation.run_id`` is the *physical* execution identity: the Dagster run
  id bound by ``phlo.telemetry.dagster_run_scope`` inside asset execution, a dlt
  load-scoped id, or a dbt invocation id. The hook event's own ``run_id`` is
  Phlo's *logical* run id (``phlo/run_id`` tag / root retry chain); it is
  preserved as ``attributes.phlo_run_id`` so retries share it while each
  attempt keeps its own run row — the observer's run-status precedence is
  monotonic and a failed first attempt must not poison a retried run.
- ``trace_id``/``span_id`` are forwarded verbatim from the hook correlation
  *when upstream code populates them* (e.g. a caller that already stamped OTel
  ids onto the event). Phlo does not currently bridge the OTel trace context
  into ``HookCorrelation``, so in practice observe events carry the trace ids
  observe-core generates per operation scope; hook-level ``trace_id`` is None
  and no field is fabricated. Wiring ``HookCorrelation.trace_id`` to the active
  OTel context would make observe events join the same trace, but that bridge
  does not exist yet — do not read the forwarded key as OTel-linked today.
- Canonical keys map directly: ``job_name -> job_id``, ``partition_key``,
  ``asset_key``, ``branch``, ``request_id``. Everything else lands in
  ``attributes``.
- Producer identity is *not* forwarded: inside a bound scope the ambient
  producer applies, so run entities derive as ``run://dagster/<id>`` etc.
  consistently regardless of which package emitted the hook event. The hook
  event's own producer is preserved as ``attributes.phlo_producer``.

The plugin is a no-op when the ``phlo-observe`` SDK is not installed or
observability is disabled; translation failures are logged by the hook bus
(``FailurePolicy.LOG``) and never abort pipeline work.
"""

from __future__ import annotations

from collections.abc import Callable
import importlib
from typing import Any

import phlo.telemetry as phlo_observe
from phlo.hooks.events import (
    DataMigrationEvent,
    HookEvent,
    IngestionEvent,
    LineageEvent,
    PublishEvent,
    QualityResultEvent,
    RunEvidenceObservationEvent,
    SchemaMigrationEvent,
    ServiceLifecycleEvent,
    TransformEvent,
)
from phlo.plugins.base import PluginMetadata
from phlo.plugins.hooks import FailurePolicy, HookPlugin, HookRegistration

_OUTCOME_SUCCESS = {"success", "succeeded", "ok", "done", "complete", "completed", "no_data"}
_OUTCOME_FAILURE = {"failure", "failed", "error", "errored"}
_OUTCOME_CANCELLED = {"cancelled", "canceled"}
_OUTCOME_PARTIAL = {"partial", "skipped", "degraded"}

_AMBIENT_RUN = object()


def _outcome(status: Any) -> str:
    """Map a hook status string onto canonical event outcomes."""
    normalized = str(status or "").strip().lower()
    if normalized in _OUTCOME_SUCCESS:
        return "success"
    if normalized in _OUTCOME_FAILURE:
        return "failure"
    if normalized in _OUTCOME_CANCELLED:
        return "cancelled"
    if normalized in _OUTCOME_PARTIAL:
        return "partial"
    return "unknown"


def _severity(status: Any, *, failed: bool = False) -> str:
    if failed:
        return "error"
    normalized = str(status or "").strip().lower()
    if normalized in _OUTCOME_FAILURE:
        return "error"
    if normalized in _OUTCOME_PARTIAL:
        return "warn"
    return "info"


def _error_info(message: str | None) -> Any:
    if not message:
        return None
    try:
        models = importlib.import_module("observe_core.models")
        return models.ErrorInfo(message=str(message))
    except Exception:  # noqa: BLE001
        return message


def _entities(**roles: tuple[Callable[..., Any], tuple[Any, ...] | None]) -> dict[str, str]:
    """Build the entities dict; each value is ``(builder, args)``."""
    out: dict[str, str] = {}
    for role, (builder, args) in roles.items():
        if not args or any(arg is None or arg == "" for arg in args):
            continue
        try:
            out[role] = str(builder(*args))
        except Exception:  # noqa: BLE001 - skip unbuildable identifiers
            continue
    return out


def _identifiers() -> Any:
    try:
        return importlib.import_module("observe_core.identifiers")
    except Exception:  # noqa: BLE001
        return None


def _field(event: HookEvent, key: str) -> Any:
    """Return a correlation value, falling back to the event's own field.

    Emitters populate correlation inconsistently: ``IngestionEvent`` carries
    ``run_id``/``partition_key`` as dataclass fields while other producers use
    ``HookCorrelation``. Correlation wins when both are set.
    """
    corr = getattr(event, "correlation", None)
    return getattr(corr, key, None) or getattr(event, key, None)


def _asset_entity_args(asset_key: Any) -> tuple[str] | None:
    """Return the asset key as a single ``asset_id`` builder arg.

    ``asset_id("raw.events")`` produces ``asset://raw.events`` — the same
    identifier the observer derives from ``correlation.asset_key``. Splitting
    into ``(group, name)`` would derive a second ``asset://raw/events``
    entity for the same asset.
    """
    key = str(asset_key or "").strip()
    if not key or key == "__pipeline__":
        return None
    return (key,)


class ObserveHookPlugin(HookPlugin):
    """Emit canonical phlo-observe events from Phlo hook events."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for discovery and identification."""
        return PluginMetadata(
            name="observe",
            version="0.1.0",
            description="Canonical phlo-observe event emission for Phlo",
        )

    def get_hooks(self) -> list[HookRegistration]:
        """Return one registration covering every translated event family."""
        return [
            HookRegistration(
                hook_name="observe_events",
                handler=self._handle,
                failure_policy=FailurePolicy.LOG,
            ),
        ]

    # -- dispatch ---------------------------------------------------------

    def _handle(self, event: HookEvent) -> None:
        """Translate one hook event into canonical observe events."""
        # Cheap gate first: when the SDK is absent or observability is
        # disabled, translation work (and its importlib lookups) is pure
        # waste — emit() would drop the result at the runtime regardless.
        if not phlo_observe.enabled():
            return
        if isinstance(event, IngestionEvent):
            self._handle_ingestion(event)
        elif isinstance(event, TransformEvent):
            self._handle_transform(event)
        elif isinstance(event, QualityResultEvent):
            self._handle_quality(event)
        elif isinstance(event, PublishEvent):
            self._handle_publish(event)
        elif isinstance(event, RunEvidenceObservationEvent):
            self._handle_observation(event)
        elif isinstance(event, LineageEvent):
            self._handle_lineage(event)
        elif isinstance(event, ServiceLifecycleEvent):
            self._handle_service(event)
        elif isinstance(event, SchemaMigrationEvent):
            self._handle_schema_migration(event)
        elif isinstance(event, DataMigrationEvent):
            self._handle_data_migration(event)
        # LogEvent/TelemetryEvent stay on the logging/OTel planes; they are
        # not operation-level wide events.

    # -- shared correlation/attributes ------------------------------------

    def _correlation(
        self,
        event: HookEvent,
        *,
        run_id: str | None | object = _AMBIENT_RUN,
    ) -> dict[str, Any]:
        """Build observe correlation from hook correlation plus ambient scope.

        ``run_id`` defaults to the ambient (physical execution) id only; pass
        ``run_id=None`` for events that must never own run membership.
        """
        out: dict[str, Any] = {}
        if run_id is _AMBIENT_RUN:
            ambient = phlo_observe.ambient_run_id()
            if ambient:
                out["run_id"] = ambient
            root = phlo_observe.ambient_root_run_id()
            if root:
                out["root_run_id"] = root
        elif run_id:
            out["run_id"] = run_id
            out["root_run_id"] = phlo_observe.ambient_root_run_id() or run_id
        for key in (
            "job_name",
            "partition_key",
            "asset_key",
            "trace_id",
            "span_id",
            "request_id",
        ):
            value = _field(event, key)
            if value:
                out["job_id" if key == "job_name" else key] = value
        return out

    def _attributes(self, event: HookEvent, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Carry hook-specific correlation into event attributes."""
        corr = getattr(event, "correlation", None)
        attrs: dict[str, Any] = {"phlo_event_type": event.event_type}
        event_id = getattr(event, "event_id", None)
        if event_id:
            attrs["phlo_event_id"] = event_id
        producer = getattr(event, "producer", None)
        if producer and producer != "phlo":
            attrs["phlo_producer"] = producer
        for key, attr in (
            ("run_id", "phlo_run_id"),
            ("project_id", "project_id"),
            ("job_name", "job_name"),
            ("partition_key", "partition_key"),
            ("check_name", "check_name"),
        ):
            value = _field(event, key)
            if value:
                attrs[attr] = value
        attempt = getattr(corr, "attempt", None)
        if attempt:
            attrs["attempt"] = attempt
        tags = getattr(event, "tags", None)
        if tags:
            attrs["phlo_tags"] = dict(tags)
        if extra:
            attrs.update(extra)
        return attrs

    def _emit(
        self,
        name: str,
        event: HookEvent,
        *,
        category: str,
        outcome: str | None = None,
        severity: str | None = None,
        delivery: str | None = None,
        attributes: dict[str, Any] | None = None,
        correlation: dict[str, Any] | None = None,
        entities: dict[str, Any] | None = None,
        run_id: str | None | object = _AMBIENT_RUN,
    ) -> None:
        corr = self._correlation(event, run_id=run_id)
        if correlation:
            corr.update(correlation)
        phlo_observe.emit(
            name,
            category=category,
            outcome=outcome,
            severity=severity,
            delivery=delivery,
            attributes=self._attributes(event, attributes),
            correlation=corr,
            entities=entities,
            error=_error_info(getattr(event, "error", None)),
            started_at=getattr(event, "timestamp", None),
        )

    # -- translations ------------------------------------------------------

    def _handle_ingestion(self, event: IngestionEvent) -> None:
        ids = _identifiers()
        # branch_name is the run's staging ref; catalog_system names the owner
        # ("nessie" for branch WAP, the snapshot catalog for snapshot WAP).
        catalog_system = _field(event, "catalog_system") or "nessie"
        entities = (
            _entities(
                asset=(ids.asset_id, _asset_entity_args(_field(event, "asset_key"))),
                table=(ids.table_id, (event.table_name,)),
                branch=(ids.branch_id, (catalog_system, event.branch_name)),
            )
            if ids
            else {}
        )
        extra = {
            "table_name": event.table_name,
            "group_name": event.group_name,
            "branch_name": event.branch_name,
            "catalog_system": catalog_system if event.branch_name else None,
            "status": event.status,
            **event.metrics,
        }
        # correlation.branch would derive branch://<producer>/<b>; the explicit
        # entities entry keeps the canonical branch://<system>/<b> identifier.
        correlation = {"table": event.table_name}
        if event.branch_name:
            correlation["branch"] = event.branch_name
        if event.event_type == "ingestion.start":
            self._emit(
                "ingestion.extract",
                event,
                category="data",
                attributes=extra,
                correlation=correlation,
                entities=entities,
            )
        elif event.event_type == "ingestion.end":
            self._emit(
                "ingestion.load",
                event,
                category="data",
                outcome=_outcome(event.status),
                severity=_severity(event.status),
                attributes=extra,
                correlation=correlation,
                entities=entities,
            )

    def _handle_transform(self, event: TransformEvent) -> None:
        ids = _identifiers()
        entities = (
            _entities(asset=(ids.asset_id, _asset_entity_args(_field(event, "asset_key"))))
            if ids
            else {}
        )
        extra = {
            "tool": event.tool,
            "target": event.target,
            "model_names": list(event.model_names) if event.model_names else None,
            "status": event.status,
            **event.metrics,
        }
        if event.event_type == "transform.start":
            self._emit(
                "transform.execute",
                event,
                category="data",
                attributes=extra,
                entities=entities,
            )
        elif event.event_type == "transform.end":
            self._emit(
                "transform.execute",
                event,
                category="data",
                outcome=_outcome(event.status),
                severity=_severity(event.status),
                attributes=extra,
                entities=entities,
            )

    def _handle_quality(self, event: QualityResultEvent) -> None:
        ids = _identifiers()
        metadata = event.metadata or {}
        if event.check_name == "wap.aggregate":
            # The WAP audit verdict is the canonical wap.validate event, not a
            # generic check: it decides promotion and is delivery-critical.
            decision = str(metadata.get("decision") or "")
            outcome = {
                "passed": "success",
                "passed_with_warnings": "partial",
                "rejected": "failure",
            }.get(decision, "success" if event.passed else "failure")
            dagster_run_id = metadata.get("dagster_run_id")
            self._emit(
                "wap.validate",
                event,
                category="wap",
                delivery="critical",
                outcome=outcome,
                severity=(
                    "error"
                    if outcome == "failure"
                    else ("warn" if outcome == "partial" else "info")
                ),
                attributes={
                    "check_name": event.check_name,
                    "check_type": event.check_type,
                    "passed": event.passed,
                    **metadata,
                },
                entities=(
                    _entities(run=(ids.run_id_for, ("dagster", dagster_run_id))) if ids else {}
                ),
                run_id=dagster_run_id,
            )
            return
        entities = (
            _entities(asset=(ids.asset_id, _asset_entity_args(_field(event, "asset_key"))))
            if ids
            else {}
        )
        self._emit(
            "quality.check",
            event,
            category="quality",
            outcome="success" if event.passed else "failure",
            severity=event.severity or ("info" if event.passed else "error"),
            attributes={
                "check_name": event.check_name,
                "check_type": event.check_type,
                "passed": event.passed,
                **metadata,
            },
            entities=entities,
        )

    def _handle_publish(self, event: PublishEvent) -> None:
        ids = _identifiers()
        entities = (
            _entities(asset=(ids.asset_id, _asset_entity_args(_field(event, "asset_key"))))
            if ids
            else {}
        )
        extra = {
            "target_system": event.target_system,
            "tables": dict(event.tables) if event.tables else None,
            "status": event.status,
            **event.metrics,
        }
        outcome = "unknown" if event.event_type == "publish.start" else _outcome(event.status)
        self._emit(
            "phlo.publish",
            event,
            category="data",
            outcome=outcome,
            severity=_severity(event.status),
            attributes=extra,
            entities=entities,
        )

    def _handle_observation(self, event: RunEvidenceObservationEvent) -> None:
        ids = _identifiers()
        change = event.catalog_change
        if not change:
            # Evidence-only observations are stage receipts; keep them visible
            # on the run timeline without inventing a canonical name.
            self._emit(
                "phlo.observation",
                event,
                category="data",
                outcome=_outcome(event.status),
                severity=_severity(event.status),
                attributes={
                    "observation_type": event.observation_type,
                    "status": event.status,
                    "run_status": event.run_status,
                    "stage_id": event.stage_id,
                    "metrics": event.metrics or None,
                    "resource_count": len(event.resources) if event.resources else None,
                    "artifact_count": len(event.artifacts) if event.artifacts else None,
                },
            )
            return
        operation = str(change.get("operation") or "")
        catalog_ref = change.get("catalog_ref")
        # The audit ref (phlo/wap_branch tag) is the lifecycle's branch;
        # catalog_ref is the ref the operation mutated ("main" on promote).
        branch = change.get("wap_branch") or catalog_ref
        dagster_run_id = change.get("dagster_run_id")
        # The staging ref's owning system — "nessie" for branch WAP, the
        # resolved snapshot catalog (e.g. polaris) for snapshot WAP.
        catalog_system = change.get("catalog_system") or "nessie"
        attrs = {
            "observation_type": event.observation_type,
            "status": event.status,
            "operation": operation,
            "catalog_ref": catalog_ref,
            "catalog_system": catalog_system,
            "source_hash": change.get("source_hash"),
            "target_hash": change.get("target_hash"),
            "merge_outcome": change.get("merge_outcome"),
            "quality_decision_id": change.get("quality_decision_id"),
        }
        entities = (
            _entities(
                branch=(ids.branch_id, (catalog_system, branch)),
                run=(ids.run_id_for, ("dagster", dagster_run_id)),
            )
            if ids
            else {}
        )
        merge_outcome = str(change.get("merge_outcome") or "").lower()
        status = str(event.status or "").lower()
        rejected = merge_outcome == "rejected_quality" or status == "rejected"
        failed = (
            _outcome(event.status) == "failure"
            or merge_outcome in {"failed", "rejected"}
            or rejected
        )
        incomplete = status == "incomplete"
        if operation == "promotion":
            # Quality rejections are wap.reject decisions; merge failures and
            # evidence gaps are wap.promote attempts that did not land.
            self._emit(
                "wap.reject" if rejected else "wap.promote",
                event,
                category="wap",
                delivery="critical",
                outcome=(
                    "failure"
                    if failed or rejected
                    else ("unknown" if incomplete else _outcome(event.status))
                ),
                severity="error" if failed or rejected else ("warn" if incomplete else "info"),
                attributes=attrs,
                correlation={"branch": branch},
                entities=entities,
                run_id=dagster_run_id,
            )
        elif operation == "cleanup":
            self._emit(
                "wap.cleanup",
                event,
                category="wap",
                outcome=(
                    "failure" if failed else ("partial" if incomplete else _outcome(event.status))
                ),
                severity="error" if failed else ("warn" if incomplete else "info"),
                attributes=attrs,
                correlation={"branch": branch},
                entities=entities,
                run_id=dagster_run_id,
            )
        else:
            self._emit(
                "phlo.observation",
                event,
                category="data",
                outcome="failure" if failed else _outcome(event.status),
                severity=_severity(event.status),
                attributes=attrs,
                correlation={"branch": branch},
                entities=entities,
                run_id=dagster_run_id,
            )

    def _handle_lineage(self, event: LineageEvent) -> None:
        self._emit(
            "phlo.lineage",
            event,
            category="lineage",
            outcome="success",
            attributes={
                "edges": [list(edge) for edge in event.edges] if event.edges else [],
                "asset_keys": list(event.asset_keys),
                **event.metadata,
            },
        )

    def _handle_service(self, event: ServiceLifecycleEvent) -> None:
        ids = _identifiers()
        self._emit(
            "phlo.service",
            event,
            category="infrastructure",
            outcome=_outcome(event.status),
            attributes={
                "service_name": event.service_name,
                "phase": event.phase,
                "status": event.status,
                "container_name": event.container_name,
                "project_name": event.project_name,
                **event.metadata,
            },
            entities=(_entities(service=(ids.service_id, (event.service_name,))) if ids else {}),
            run_id=None,
        )

    def _handle_schema_migration(self, event: SchemaMigrationEvent) -> None:
        ids = _identifiers()
        self._emit(
            "phlo.schema_migration",
            event,
            category="data",
            outcome=_outcome(event.status),
            attributes={
                "table_name": event.table_name,
                "classification": event.classification,
                "change_count": event.change_count,
                "status": event.status,
                "changes": event.changes or None,
            },
            entities=_entities(table=(ids.table_id, (event.table_name,))) if ids else {},
        )

    def _handle_data_migration(self, event: DataMigrationEvent) -> None:
        ids = _identifiers()
        self._emit(
            "phlo.data_migration",
            event,
            category="data",
            outcome=_outcome(event.status),
            attributes={
                "migration_name": event.migration_name,
                "source_type": event.source_type,
                "destination_table": event.destination_table,
                "status": event.status,
                "rows_read": event.rows_read,
                "rows_written": event.rows_written,
                "chunk_index": event.chunk_index,
                **event.metrics,
            },
            entities=(_entities(table=(ids.table_id, (event.destination_table,))) if ids else {}),
        )
