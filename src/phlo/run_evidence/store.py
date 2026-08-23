"""Transactional stores for the run-evidence contract.

A single SQL implementation backs both backends; SQLite serves local
development, PostgreSQL production. Reusing an event identity with different
content raises IdempotencyConflict. Payloads are redacted and resource
identity checksummed for tamper evidence; migrations verify their checksums.

Backing store for run evidence: imported by phlo-api observatory endpoints and
reconciliation callers.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from phlo.run_evidence.models import (
    RUN_EVIDENCE_SCHEMA_VERSION,
    EvidenceCompleteness,
    PipelineRun,
    RunArtifact,
    RunCatalogChange,
    RunEvent,
    RunLineageEdge,
    RunQualityResult,
    RunResource,
    RunStage,
    _positive_attempt,
)
from phlo.run_evidence.reconciliation import (
    DEFAULT_CLOCK_SKEW,
    TERMINAL_STATUSES,
    ReconciliationDecision,
    RequiredEvidenceProfile,
    RunEvidenceNotFound,
    RunLookupOutcome,
    RunObservation,
    evaluate_reconciliation,
    normalize_status,
)
from phlo.run_evidence.redaction import canonical_json, payload_checksum, redact_payload

_RUN_EVIDENCE_MIGRATIONS = (
    (1, "002_create_run_evidence.sql", "002_create_run_evidence_sqlite.sql"),
    (2, "003_reconcile_run_evidence.sql", "003_reconcile_run_evidence_sqlite.sql"),
    (3, "004_run_evidence_instrumentation.sql", "004_run_evidence_instrumentation_sqlite.sql"),
    (4, "005_run_evidence_resource_identity.sql", "005_run_evidence_resource_identity_sqlite.sql"),
    (5, "006_run_evidence_run_list_index.sql", "006_run_evidence_run_list_index_sqlite.sql"),
)


def _migration_checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _encode_run_cursor(activity: str, project_id: str, run_id: str) -> str:
    payload = json.dumps(
        {"activity": activity, "project_id": project_id, "run_id": run_id}, separators=(",", ":")
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii")


def _decode_run_cursor(cursor: str | None) -> tuple[str, str, str] | None:
    if not cursor:
        return None
    try:
        payload = json.loads(urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    values = (payload.get("activity"), payload.get("project_id"), payload.get("run_id"))
    return values if all(isinstance(value, str) and value for value in values) else None


_REPORT_ORDER_BY = {
    "pipeline_run": "attempt",
    "run_event": "sequence IS NULL, sequence, observed_at, event_id, producer",
    "run_stage": "started_at IS NULL, started_at, stage_id",
    "run_resource": "role, normalized_identity, resource_id",
    "run_lineage_edge": "source, target, lineage_edge_id",
    "run_quality_result": "check_id, quality_result_id",
    "run_catalog_change": "catalog_ref, operation, catalog_change_id",
    "run_artifact": "artifact_kind, artifact_id",
    "run_reconciliation_decision": "decided_at, decision_id",
}


class IdempotencyConflict(ValueError):
    """A producer reused an event identity with different content."""


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _canonical_timestamp(value: Any) -> str | None:
    """Return one UTC ISO-8601 representation for every SQL timestamp value."""
    if value is None:
        return None
    parsed = _parse_timestamp(value)
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _json(value: Any) -> str:
    return canonical_json(value)


def _resource_identity(value: Any) -> str | None:
    """Serialize the shared core authorization vocabulary without aliases."""
    return _json(asdict(value)) if value is not None else None


def _text(value: str | None) -> str | None:
    redacted = redact_payload(value)
    return redacted if isinstance(redacted, str) or redacted is None else str(redacted)


def _resource_checksum_payload(resource: RunResource) -> dict[str, Any]:
    payload = {key: value for key, value in asdict(resource).items() if key != "resource_id"}
    for key in ("schema_hash_before", "schema_hash_after", "metadata", "resource_ref"):
        if payload.get(key) in (None, {}):
            payload.pop(key, None)
    return payload


def _lineage_checksum_payload(edge: RunLineageEdge) -> dict[str, Any]:
    payload = {key: value for key, value in asdict(edge).items() if key != "lineage_edge_id"}
    if edge.attempt == 1:
        payload.pop("attempt", None)
    for key in ("source_resource_ref", "target_resource_ref"):
        if payload.get(key) is None:
            payload.pop(key, None)
    return payload


def _catalog_checksum_payload(change: RunCatalogChange) -> dict[str, Any]:
    payload = {key: value for key, value in asdict(change).items() if key != "catalog_change_id"}
    if change.quality_decision_id is None:
        payload.pop("quality_decision_id", None)
    if change.resource_ref is None:
        payload.pop("resource_ref", None)
    return payload


def _quality_checksum_payload(result: RunQualityResult) -> dict[str, Any]:
    payload = {key: value for key, value in asdict(result).items() if key != "quality_result_id"}
    if result.resource_ref is None:
        payload.pop("resource_ref", None)
    return payload


def _artifact_checksum_payload(artifact: RunArtifact) -> dict[str, Any]:
    payload = {key: value for key, value in asdict(artifact).items() if key != "artifact_id"}
    if artifact.resource_ref is None:
        payload.pop("resource_ref", None)
    return payload


_JSON_ROW_FIELDS: dict[str, dict[str, type]] = {
    "run_event": {"payload": dict},
    "run_stage": {"metrics": dict},
    "run_resource": {"staged_objects": list, "metadata": dict},
    "run_lineage_edge": {"column_mapping": dict},
    "run_catalog_change": {"metadata": dict},
    "run_quality_result": {"metadata": dict},
    "run_reconciliation_decision": {"missing_evidence": list},
}
_OPTIONAL_JSON_ROW_FIELDS: dict[str, set[str]] = {
    "run_event": {"resource_identity"},
    "run_stage": {"resource_identity"},
    "run_resource": {"resource_identity"},
    "run_lineage_edge": {"source_resource_identity", "target_resource_identity"},
    "run_catalog_change": {"resource_identity"},
    "run_quality_result": {"resource_identity"},
    "run_artifact": {"resource_identity"},
}
_BOOLEAN_ROW_FIELDS = {
    "run_quality_result": {"blocking", "passed"},
    "run_artifact": {"legal_hold"},
}
_TIMESTAMP_ROW_FIELDS = {
    "pipeline_run": {
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
        "last_heartbeat_at",
        "reconciled_at",
    },
    "run_event": {"observed_at", "created_at"},
    "run_stage": {"started_at", "finished_at"},
    "run_artifact": {"expires_at"},
    "run_reconciliation_decision": {"heartbeat_at", "decided_at", "finished_at"},
}


class _SqlRunEvidenceStore:
    """Shared SQL implementation; subclasses provide transaction connections."""

    placeholder = "?"
    table_prefix = ""

    def __init__(self) -> None:
        self._initialized = False
        self._lock = threading.RLock()

    def _connect(self) -> Any:
        raise NotImplementedError

    def _close_connection(self, connection: Any) -> None:
        connection.close()

    def _table(self, name: str) -> str:
        return f"{self.table_prefix}{name}"

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, Any]]:
        self._ensure_schema()
        with self._lock:
            connection = self._connect()
            cursor = connection.cursor()
            try:
                if self.placeholder == "?":
                    cursor.execute("BEGIN IMMEDIATE")
                yield connection, cursor
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                self._close_connection(connection)

    @contextmanager
    def _read_transaction(self) -> Iterator[tuple[Any, Any]]:
        """Open one consistent read snapshot for a report projection."""
        self._ensure_schema()
        with self._lock:
            connection = self._connect()
            cursor = connection.cursor()
            try:
                if self.placeholder == "?":
                    cursor.execute("BEGIN")
                else:
                    cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                yield connection, cursor
            finally:
                try:
                    connection.rollback()
                finally:
                    try:
                        cursor.close()
                    finally:
                        self._close_connection(connection)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._initialize_schema()
            self._initialized = True

    def _initialize_schema(self) -> None:
        raise NotImplementedError

    def initialize(self) -> None:
        """Run migrations before the store starts serving requests."""
        self._ensure_schema()

    def close(self) -> None:
        """Release store-owned resources."""

    def append_pipeline_run(self, run: PipelineRun) -> None:
        """Upsert a pipeline run, keyed by project and run id."""
        with self._transaction() as (_, cursor):
            self._upsert_run(cursor, run)

    def append_event(
        self,
        event: RunEvent,
        *,
        run: PipelineRun | None = None,
        stage: RunStage | None = None,
        quality_result: RunQualityResult | None = None,
        lineage_edges: tuple[RunLineageEdge, ...] = (),
        resources: tuple[RunResource, ...] = (),
        catalog_change: RunCatalogChange | None = None,
        artifacts: tuple[RunArtifact, ...] = (),
    ) -> bool:
        """Append one event and optional derived records in one transaction."""
        self._validate_append_event_inputs(
            event,
            run=run,
            stage=stage,
            quality_result=quality_result,
            lineage_edges=lineage_edges,
            resources=resources,
            catalog_change=catalog_change,
            artifacts=artifacts,
        )
        with self._transaction() as (_, cursor):
            existing = self._event_identity(cursor, event)
            # Preserve deterministic cross-run replay conflicts even when the
            # forged target parent does not exist and cannot be locked.
            if existing is not None and existing[0] != event.run_id:
                return self._insert_event(cursor, event)

            # Check references before creating a placeholder parent, then
            # repeat after locking so a concurrent stage write cannot slip
            # between the read and the event insert.
            self._validate_event_stage_references(cursor, event, stage, quality_result)

            # Establish a lockable parent without applying provider-derived
            # replay metadata. A concurrent delivery may otherwise both see
            # no event and update the parent before one loses the event race.
            self._ensure_event_parent(cursor, event, run)
            self._lock_run(cursor, event.project_id, event.run_id)
            self._validate_event_stage_references(cursor, event, stage, quality_result)
            # The parent lock makes this re-check linearizable with every
            # writer that follows the same parent-locking protocol.
            existing = self._event_identity(cursor, event)
            inserted = self._insert_event(cursor, event) if existing is None else False
            if existing is not None:
                # _insert_event validates payload, attempt, and correlation,
                # while returning False for an identical replay.
                self._insert_event(cursor, event)
            if inserted:
                if run is not None:
                    self._upsert_run(cursor, run)
                if stage is not None:
                    self._insert_stage(cursor, stage)
                if quality_result is not None:
                    self._insert_quality(cursor, quality_result)
                for edge in lineage_edges:
                    self._insert_lineage(cursor, edge)
                for resource in resources:
                    self._insert_resource(cursor, resource)
                if catalog_change is not None:
                    self._insert_catalog_change(cursor, catalog_change)
                for artifact in artifacts:
                    self._insert_artifact(cursor, artifact)
            return inserted

    def _validate_append_event_inputs(
        self,
        event: RunEvent,
        *,
        run: PipelineRun | None,
        stage: RunStage | None,
        quality_result: RunQualityResult | None,
        lineage_edges: tuple[RunLineageEdge, ...],
        resources: tuple[RunResource, ...],
        catalog_change: RunCatalogChange | None,
        artifacts: tuple[RunArtifact, ...],
    ) -> None:
        """Reject cross-boundary bundles before a placeholder parent can be created."""
        self._require_id("project_id", event.project_id)
        self._require_id("run_id", event.run_id)
        self._require_id("event_id", event.event_id)
        self._require_id("producer", event.producer)
        if event.attempt <= 0:
            raise ValueError("event attempt must be positive")

        def validate_object(
            label: str,
            value: Any,
            *,
            object_id: str | None = None,
            attempt: int | None = None,
        ) -> None:
            """Reject objects that cross project, run, or attempt boundaries."""
            if value is None:
                return
            if value.project_id != event.project_id or value.run_id != event.run_id:
                raise ValueError(f"{label} crossed project/run boundaries")
            if attempt is not None and attempt != event.attempt:
                raise ValueError(f"{label} crossed attempt boundaries")
            if object_id is not None:
                self._require_id(f"{label}_id", object_id)

        validate_object("run", run, attempt=run.attempt if run is not None else None)
        validate_object(
            "stage",
            stage,
            object_id=stage.stage_id if stage is not None else None,
            attempt=stage.attempt if stage is not None else None,
        )
        validate_object(
            "quality_result",
            quality_result,
            object_id=quality_result.quality_result_id if quality_result is not None else None,
            attempt=quality_result.attempt if quality_result is not None else None,
        )
        stage_ids = {
            stage_id
            for stage_id in (
                event.stage_id,
                stage.stage_id if stage is not None else None,
                quality_result.stage_id if quality_result is not None else None,
            )
            if stage_id is not None
        }
        if len(stage_ids) > 1:
            raise ValueError("optional evidence has conflicting stage identities")
        for edge in lineage_edges:
            validate_object(
                "lineage_edge", edge, object_id=edge.lineage_edge_id, attempt=edge.attempt
            )
        for resource in resources:
            validate_object(
                "resource", resource, object_id=resource.resource_id, attempt=resource.attempt
            )
        if catalog_change is not None:
            validate_object(
                "catalog_change",
                catalog_change,
                object_id=catalog_change.catalog_change_id,
                attempt=catalog_change.attempt,
            )
        for artifact in artifacts:
            validate_object(
                "artifact", artifact, object_id=artifact.artifact_id, attempt=artifact.attempt
            )

    def _event_identity(self, cursor: Any, event: RunEvent) -> tuple[Any, ...] | None:
        cursor.execute(
            f"SELECT run_id, event_type, schema_version, stage_id, attempt, payload_checksum "
            f"FROM {self._table('run_event')} "
            f"WHERE project_id = {self.placeholder} AND producer = {self.placeholder} "
            f"AND event_id = {self.placeholder}",
            (event.project_id, event.producer, event.event_id),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    def _ensure_event_parent(self, cursor: Any, event: RunEvent, run: PipelineRun | None) -> None:
        cursor.execute(
            f"SELECT 1 FROM {self._table('pipeline_run')} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
            (event.project_id, event.run_id),
        )
        if cursor.fetchone() is not None:
            return
        if run is None:
            raise ValueError(f"run {event.project_id}/{event.run_id} does not exist")
        values = (
            event.run_id,
            event.project_id,
            run.attempt,
            "running",
            EvidenceCompleteness.INCOMPLETE.value,
        )
        cursor.execute(
            f"INSERT INTO {self._table('pipeline_run')} "
            "(run_id, project_id, attempt, status, evidence_completeness) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id) DO NOTHING",
            values,
        )

    def append_stage(self, stage: RunStage) -> None:
        """Insert a stage record under the run's write lock."""
        with self._transaction() as (_, cursor):
            self._lock_run(cursor, stage.project_id, stage.run_id)
            self._insert_stage(cursor, stage)

    def append_resource(self, resource: RunResource) -> None:
        """Insert a resource record under the run's write lock."""
        with self._transaction() as (_, cursor):
            self._lock_run(cursor, resource.project_id, resource.run_id)
            self._insert_resource(cursor, resource)

    def append_lineage_edge(self, edge: RunLineageEdge) -> None:
        """Insert a lineage edge under the run's write lock."""
        with self._transaction() as (_, cursor):
            self._lock_run(cursor, edge.project_id, edge.run_id)
            self._insert_lineage(cursor, edge)

    def append_quality_result(self, result: RunQualityResult) -> None:
        """Insert a quality result under the run's write lock."""
        with self._transaction() as (_, cursor):
            self._lock_run(cursor, result.project_id, result.run_id)
            self._insert_quality(cursor, result)

    def append_catalog_change(self, change: RunCatalogChange) -> None:
        """Insert a catalog change under the run's write lock."""
        with self._transaction() as (_, cursor):
            self._lock_run(cursor, change.project_id, change.run_id)
            self._insert_catalog_change(cursor, change)

    def append_artifact(self, artifact: RunArtifact) -> None:
        """Insert an artifact record under the run's write lock."""
        with self._transaction() as (_, cursor):
            self._lock_run(cursor, artifact.project_id, artifact.run_id)
            self._insert_artifact(cursor, artifact)

    def update_run(
        self,
        project_id: str,
        run_id: str,
        *,
        status: str | None = None,
        finished_at: datetime | None = None,
        failure_summary: str | None = None,
        evidence_completeness: EvidenceCompleteness | None = None,
    ) -> None:
        """Update terminal run state without changing its identity or history."""
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = " + self.placeholder)
            values.append(status)
        if finished_at is not None:
            fields.append("finished_at = " + self.placeholder)
            values.append(_timestamp(finished_at))
        if failure_summary is not None:
            fields.append("failure_summary = " + self.placeholder)
            values.append(_text(failure_summary))
        if evidence_completeness is not None:
            fields.append("evidence_completeness = " + self.placeholder)
            values.append(evidence_completeness.value)
        if not fields:
            return
        fields.append("updated_at = " + self.placeholder)
        values.append(_timestamp(datetime.now(UTC)))
        values.extend([project_id, run_id])
        with self._transaction() as (_, cursor):
            cursor.execute(
                f"UPDATE {self._table('pipeline_run')} SET {', '.join(fields)} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
                tuple(values),
            )

    def reconcile_observation(
        self,
        observation: RunObservation,
        profile: RequiredEvidenceProfile,
        *,
        now: datetime,
        stale_after: timedelta | None,
        clock_skew: timedelta = DEFAULT_CLOCK_SKEW,
    ) -> ReconciliationDecision:
        """Ingest an observation, evaluate it, and persist the decision atomically."""
        if observation.project_id.strip() == "" or observation.run_id.strip() == "":
            raise ValueError("project_id and run_id must be stable non-empty identifiers")
        with self._transaction() as (_, cursor):
            cursor.execute(
                f"SELECT * FROM {self._table('pipeline_run')} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
                (observation.project_id, observation.run_id),
            )
            existing_before = cursor.fetchone()
            existing_before_row = (
                self._row_dict(cursor, existing_before, table="pipeline_run")
                if existing_before is not None
                else None
            )
            provider_absent = observation.lookup_outcome is RunLookupOutcome.ABSENT
            if provider_absent:
                if existing_before is None:
                    raise RunEvidenceNotFound(
                        f"provider has no durable run {observation.project_id}/{observation.run_id}"
                    )
                existing_row = existing_before_row
                assert existing_row is not None
                observation = replace(
                    observation,
                    attempt=max(observation.attempt, int(existing_row["attempt"])),
                    pipeline_name=observation.pipeline_name or existing_row.get("pipeline_name"),
                    provider_run_id=observation.provider_run_id
                    or existing_row.get("provider_run_id"),
                    status=existing_row.get("status"),
                    started_at=_parse_timestamp(existing_row.get("started_at")),
                    finished_at=_parse_timestamp(existing_row.get("finished_at")),
                    heartbeat_at=_parse_timestamp(existing_row.get("last_heartbeat_at")),
                )
            else:
                self._upsert_run(
                    cursor,
                    PipelineRun(
                        project_id=observation.project_id,
                        run_id=observation.run_id,
                        pipeline_name=observation.pipeline_name,
                        provider_run_id=observation.provider_run_id,
                        attempt=observation.attempt,
                        status=observation.status or "running",
                        started_at=observation.started_at,
                        finished_at=observation.finished_at,
                        evidence_completeness=observation.evidence_state
                        or EvidenceCompleteness.INCOMPLETE,
                    ),
                )
            self._lock_run(cursor, observation.project_id, observation.run_id)
            for event in () if provider_absent else observation.events:
                if event.project_id != observation.project_id or event.run_id != observation.run_id:
                    raise ValueError("event source crossed project/run boundaries")
                if event.attempt != observation.attempt:
                    raise ValueError("event source crossed attempt boundaries")
                self._insert_event(cursor, event)
            for stage in () if provider_absent else observation.stages:
                if stage.project_id != observation.project_id or stage.run_id != observation.run_id:
                    raise ValueError("stage source crossed project/run boundaries")
                if stage.attempt != observation.attempt:
                    raise ValueError("stage source crossed attempt boundaries")
                self._insert_stage(cursor, stage)

            cursor.execute(
                f"SELECT * FROM {self._table('pipeline_run')} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
                (observation.project_id, observation.run_id),
            )
            run_row = self._row_dict(cursor, cursor.fetchone(), table="pipeline_run")
            cursor.execute(
                f"SELECT * FROM {self._table('run_event')} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
                (observation.project_id, observation.run_id),
            )
            event_rows = [
                self._row_dict(cursor, row, table="run_event") for row in cursor.fetchall()
            ]
            cursor.execute(
                f"SELECT * FROM {self._table('run_stage')} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
                (observation.project_id, observation.run_id),
            )
            stage_rows = [
                self._row_dict(cursor, row, table="run_stage") for row in cursor.fetchall()
            ]
            record_rows: dict[str, list[dict[str, Any]]] = {}
            for family, table in (
                ("resource", "run_resource"),
                ("catalog_change", "run_catalog_change"),
                ("quality_result", "run_quality_result"),
                ("artifact", "run_artifact"),
            ):
                cursor.execute(
                    f"SELECT * FROM {self._table(table)} "
                    f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
                    f"AND attempt = {self.placeholder}",
                    (observation.project_id, observation.run_id, observation.attempt),
                )
                record_rows[family] = [
                    self._row_dict(cursor, row, table=table) for row in cursor.fetchall()
                ]
            decision = evaluate_reconciliation(
                observation=observation,
                profile=profile,
                run_row=run_row,
                event_rows=event_rows,
                stage_rows=stage_rows,
                record_rows=record_rows,
                now=now,
                stale_after=stale_after,
                clock_skew=clock_skew,
            )
            stored_decision, inserted = self._insert_reconciliation_decision(cursor, decision)
            if not inserted:
                if existing_before_row is not None:
                    self._restore_run_updated_at(
                        cursor,
                        observation.project_id,
                        observation.run_id,
                        existing_before_row.get("updated_at"),
                    )
                return stored_decision

            if provider_absent:
                cursor.execute(
                    f"UPDATE {self._table('pipeline_run')} SET evidence_completeness = {self.placeholder}, "
                    f"reconciled_at = {self.placeholder}, reconciliation_reason = {self.placeholder}, "
                    f"updated_at = {self.placeholder} WHERE project_id = {self.placeholder} "
                    f"AND run_id = {self.placeholder}",
                    (
                        decision.evidence_completeness.value,
                        _timestamp(decision.decided_at),
                        _text(decision.reason),
                        _timestamp(datetime.now(UTC)),
                        observation.project_id,
                        observation.run_id,
                    ),
                )
                return decision

            current_attempt = int(run_row["attempt"])
            current_status = normalize_status(str(run_row["status"])) or "running"
            aggregate_status = decision.status
            aggregate_finished_at: datetime | str | None = decision.finished_at
            # A terminal status already stored for this attempt wins over a
            # contradicting later observation, lower attempts never rewrite a
            # higher one, and a terminal run may not regress to a non-terminal
            # status. The guards below encode those three precedence rules.
            if (
                current_attempt == observation.attempt
                and current_status in TERMINAL_STATUSES
                and decision.status in TERMINAL_STATUSES
                and decision.status != current_status
            ):
                aggregate_status = current_status
                aggregate_finished_at = run_row.get("finished_at")
            if observation.attempt >= current_attempt and not (
                current_attempt == observation.attempt
                and current_status in TERMINAL_STATUSES
                and decision.status in {"running", "incomplete", "abandoned"}
            ):
                cursor.execute(
                    f"UPDATE {self._table('pipeline_run')} SET status = {self.placeholder}, "
                    f"finished_at = {self.placeholder}, evidence_completeness = {self.placeholder}, "
                    f"last_heartbeat_at = {self.placeholder}, reconciled_at = {self.placeholder}, "
                    f"reconciliation_reason = {self.placeholder}, updated_at = {self.placeholder} "
                    f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
                    f"AND attempt = {self.placeholder}",
                    (
                        aggregate_status,
                        _timestamp(aggregate_finished_at)
                        if isinstance(aggregate_finished_at, datetime)
                        else aggregate_finished_at,
                        decision.evidence_completeness.value,
                        _timestamp(decision.heartbeat_at),
                        _timestamp(decision.decided_at),
                        _text(decision.reason),
                        _timestamp(datetime.now(UTC)),
                        observation.project_id,
                        observation.run_id,
                        observation.attempt,
                    ),
                )
            return decision

    def list_reconciliation_decisions(self, project_id: str, run_id: str) -> list[dict[str, Any]]:
        """Return immutable reconciliation snapshots in evaluation order."""
        with self._transaction() as (_, cursor):
            cursor.execute(
                f"SELECT * FROM {self._table('run_reconciliation_decision')} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
                f"ORDER BY {_REPORT_ORDER_BY['run_reconciliation_decision']}",
                (project_id, run_id),
            )
            return [
                self._row_dict(cursor, row, table="run_reconciliation_decision")
                for row in cursor.fetchall()
            ]

    def list_runs(self) -> list[dict[str, Any]]:
        """Return every durable pipeline run, newest activity first."""
        with self._read_transaction() as (_, cursor):
            cursor.execute(
                f"SELECT * FROM {self._table('pipeline_run')} "
                f"ORDER BY COALESCE(finished_at, started_at) DESC, "
                f"project_id, run_id",
            )
            return [self._row_dict(cursor, row, table="pipeline_run") for row in cursor.fetchall()]

    def list_runs_page(
        self, *, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return one stable, bounded page of durable runs.

        The cursor captures the final row's activity timestamp and immutable run
        identity, avoiding offset drift when rows are inserted or deleted.
        """
        limit = max(1, min(limit, 500))
        position = _decode_run_cursor(cursor)
        activity = "COALESCE(finished_at, started_at, created_at)"
        where = ""
        parameters: list[Any] = []
        if position is not None:
            where = (
                f"WHERE ({activity} < {self.placeholder} OR "
                f"({activity} = {self.placeholder} AND (project_id > {self.placeholder} OR "
                f"(project_id = {self.placeholder} AND run_id > {self.placeholder}))))"
            )
            parameters.extend((position[0], position[0], position[1], position[1], position[2]))
        parameters.append(limit + 1)
        with self._read_transaction() as (_, sql_cursor):
            sql_cursor.execute(
                f"SELECT *, {activity} AS _activity FROM {self._table('pipeline_run')} {where} "
                f"ORDER BY {activity} DESC, project_id ASC, run_id ASC LIMIT {self.placeholder}",
                tuple(parameters),
            )
            rows = [
                self._row_dict(sql_cursor, row, table="pipeline_run")
                for row in sql_cursor.fetchall()
            ]
        has_next = len(rows) > limit
        page = rows[:limit]
        if not has_next or not page:
            return page, None
        last = page[-1]
        return page, _encode_run_cursor(
            str(last["_activity"]), str(last["project_id"]), str(last["run_id"])
        )

    def read_run_attempt(
        self, project_id: str, run_id: str, attempt: int
    ) -> dict[str, list[dict[str, Any]] | dict[str, Any] | None]:
        """Read every report family from one transaction snapshot."""
        attempt = _positive_attempt(attempt)
        with self._read_transaction() as (_, cursor):
            run = self._select_snapshot_rows(
                cursor, "pipeline_run", project_id, run_id, attempt, "attempt"
            )
            result: dict[str, list[dict[str, Any]] | dict[str, Any] | None] = {
                "run": run[0] if run else None
            }
            for family, table in (
                ("events", "run_event"),
                ("stages", "run_stage"),
                ("resources", "run_resource"),
                ("lineage", "run_lineage_edge"),
                ("quality", "run_quality_result"),
                ("catalog_changes", "run_catalog_change"),
                ("artifacts", "run_artifact"),
            ):
                result[family] = self._select_snapshot_rows(
                    cursor, table, project_id, run_id, attempt, _REPORT_ORDER_BY[table]
                )
            result["reconciliation"] = self._select_snapshot_rows(
                cursor,
                "run_reconciliation_decision",
                project_id,
                run_id,
                attempt,
                _REPORT_ORDER_BY["run_reconciliation_decision"],
            )
            return result

    def _select_snapshot_rows(
        self,
        cursor: Any,
        table: str,
        project_id: str,
        run_id: str,
        attempt: int,
        order_by: str,
    ) -> list[dict[str, Any]]:
        cursor.execute(
            f"SELECT * FROM {self._table(table)} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
            f"AND attempt = {self.placeholder} ORDER BY {order_by}",
            (project_id, run_id, attempt),
        )
        return [self._row_dict(cursor, row, table=table) for row in cursor.fetchall()]

    def get_run(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> dict[str, Any] | None:
        """Return the pipeline run row for a project/run, optionally scoped to
        one attempt, or None when absent.
        """
        with self._transaction() as (_, cursor):
            where = f"project_id = {self.placeholder} AND run_id = {self.placeholder}"
            params: tuple[Any, ...] = (project_id, run_id)
            if attempt is not None:
                attempt = _positive_attempt(attempt)
                where += f" AND attempt = {self.placeholder}"
                params += (attempt,)
            cursor.execute(
                f"SELECT * FROM {self._table('pipeline_run')} WHERE {where}",
                params,
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_dict(cursor, row, table="pipeline_run")

    def list_events(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the run's events in deterministic report order, optionally
        scoped to one attempt.
        """
        with self._transaction() as (_, cursor):
            where = f"project_id = {self.placeholder} AND run_id = {self.placeholder}"
            params: tuple[Any, ...] = (project_id, run_id)
            if attempt is not None:
                attempt = _positive_attempt(attempt)
                where += f" AND attempt = {self.placeholder}"
                params += (attempt,)
            cursor.execute(
                f"SELECT * FROM {self._table('run_event')} "
                f"WHERE {where} "
                f"ORDER BY {_REPORT_ORDER_BY['run_event']}",
                params,
            )
            return [self._row_dict(cursor, row, table="run_event") for row in cursor.fetchall()]

    def list_stages(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return stages in deterministic execution order for one attempt."""
        return self._list_attempt_records("run_stage", project_id, run_id, attempt=attempt)

    def count_events(self, project_id: str, run_id: str) -> int:
        """Count the events recorded for a run."""
        with self._transaction() as (_, cursor):
            cursor.execute(
                f"SELECT COUNT(*) FROM {self._table('run_event')} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
                (project_id, run_id),
            )
            return int(cursor.fetchone()[0])

    def list_resources(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the run's resources in deterministic order for one attempt."""
        return self._list_attempt_records("run_resource", project_id, run_id, attempt=attempt)

    def list_catalog_changes(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the run's catalog changes in deterministic order for one
        attempt.
        """
        return self._list_attempt_records("run_catalog_change", project_id, run_id, attempt=attempt)

    def list_quality_results(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the run's quality results in deterministic order for one
        attempt.
        """
        return self._list_attempt_records("run_quality_result", project_id, run_id, attempt=attempt)

    def list_artifacts(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the run's artifacts in deterministic order for one attempt."""
        return self._list_attempt_records("run_artifact", project_id, run_id, attempt=attempt)

    def list_lineage_edges(
        self, project_id: str, run_id: str, *, attempt: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the run's lineage edges in deterministic order for one
        attempt.
        """
        return self._list_attempt_records("run_lineage_edge", project_id, run_id, attempt=attempt)

    def _list_attempt_records(
        self,
        table: str,
        project_id: str,
        run_id: str,
        *,
        attempt: int | None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        if attempt is not None:
            attempt = _positive_attempt(attempt)
        where = f"project_id = {self.placeholder} AND run_id = {self.placeholder}"
        params: tuple[Any, ...] = (project_id, run_id)
        if attempt is not None:
            where += f" AND attempt = {self.placeholder}"
            params += (attempt,)
        order_by = order_by or _REPORT_ORDER_BY.get(table, "record_checksum")
        with self._transaction() as (_, cursor):
            cursor.execute(
                f"SELECT * FROM {self._table(table)} WHERE {where} ORDER BY {order_by}",
                params,
            )
            return [self._row_dict(cursor, row, table=table) for row in cursor.fetchall()]

    def _upsert_run(self, cursor: Any, run: PipelineRun) -> None:
        cursor.execute(
            f"SELECT project_id FROM {self._table('pipeline_run')} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
            (run.project_id, run.run_id),
        )
        columns = (
            "run_id, project_id, pipeline_name, provider_run_id, trigger, initiator, "
            "effective_identity, partition_key, code_version, config_version, attempt, "
            "trace_id, status, started_at, finished_at, failure_summary, evidence_completeness"
        )
        values = (
            run.run_id,
            run.project_id,
            _text(run.pipeline_name),
            _text(run.provider_run_id),
            _text(run.trigger),
            _text(run.initiator),
            _text(run.effective_identity),
            _text(run.partition_key),
            _text(run.code_version),
            _text(run.config_version),
            run.attempt,
            _text(run.trace_id),
            _text(run.status),
            _timestamp(run.started_at),
            _timestamp(run.finished_at),
            _text(run.failure_summary),
            run.evidence_completeness.value,
        )
        updates = ", ".join(
            [
                f"{field} = COALESCE(existing_run.{field}, EXCLUDED.{field})"
                for field in (
                    "pipeline_name",
                    "provider_run_id",
                    "trigger",
                    "initiator",
                    "partition_key",
                )
            ]
            + [
                # provider_run_id identifies the logical provider run, while
                # these fields describe the latest execution attempt. A
                # same-attempt replay only fills a missing value so late
                # lower-attempt evidence cannot rewrite current provenance.
                "effective_identity = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.effective_identity WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.effective_identity ELSE COALESCE(existing_run.effective_identity, EXCLUDED.effective_identity) END",
                "code_version = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.code_version WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.code_version ELSE COALESCE(existing_run.code_version, EXCLUDED.code_version) END",
                "config_version = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.config_version WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.config_version ELSE COALESCE(existing_run.config_version, EXCLUDED.config_version) END",
                "started_at = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.started_at WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.started_at ELSE COALESCE(existing_run.started_at, EXCLUDED.started_at) END",
                "attempt = CASE WHEN existing_run.attempt > EXCLUDED.attempt "
                "THEN existing_run.attempt ELSE EXCLUDED.attempt END",
                "status = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.status WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.status WHEN existing_run.status IN "
                "('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
                "THEN existing_run.status ELSE EXCLUDED.status END",
                "trace_id = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.trace_id WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.trace_id ELSE COALESCE(existing_run.trace_id, EXCLUDED.trace_id) END",
                "finished_at = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.finished_at WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.finished_at WHEN existing_run.status IN "
                "('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
                "THEN COALESCE(existing_run.finished_at, EXCLUDED.finished_at) "
                "ELSE COALESCE(EXCLUDED.finished_at, existing_run.finished_at) END",
                "failure_summary = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.failure_summary WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.failure_summary WHEN existing_run.status IN "
                "('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
                "THEN COALESCE(existing_run.failure_summary, EXCLUDED.failure_summary) "
                "ELSE COALESCE(EXCLUDED.failure_summary, existing_run.failure_summary) END",
                "evidence_completeness = CASE WHEN EXCLUDED.attempt > existing_run.attempt "
                "THEN EXCLUDED.evidence_completeness WHEN EXCLUDED.attempt < existing_run.attempt "
                "THEN existing_run.evidence_completeness WHEN existing_run.status IN "
                "('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
                "THEN existing_run.evidence_completeness WHEN existing_run.evidence_completeness IN "
                "('complete', 'expired', 'redacted') AND EXCLUDED.evidence_completeness = 'incomplete' "
                "THEN existing_run.evidence_completeness ELSE EXCLUDED.evidence_completeness END",
            ]
        )
        cursor.execute(
            f"INSERT INTO {self._table('pipeline_run')} AS existing_run ({columns}) VALUES "
            f"({', '.join([self.placeholder] * len(values))}) "
            f"ON CONFLICT (project_id, run_id) DO UPDATE SET {updates}, "
            "updated_at = CURRENT_TIMESTAMP",
            values,
        )

    def _restore_run_updated_at(
        self,
        cursor: Any,
        project_id: str,
        run_id: str,
        updated_at: Any,
    ) -> None:
        cursor.execute(
            f"UPDATE {self._table('pipeline_run')} SET updated_at = {self.placeholder} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}",
            (updated_at, project_id, run_id),
        )

    def _insert_event(self, cursor: Any, event: RunEvent) -> bool:
        self._require_id("event_id", event.event_id)
        self._require_id("producer", event.producer)
        redacted = redact_payload(event.payload)
        checksum = payload_checksum(redacted)
        values = (
            event.project_id,
            event.run_id,
            event.stage_id,
            event.event_id,
            event.event_type,
            event.schema_version,
            event.producer,
            _timestamp(event.observed_at),
            event.sequence,
            event.attempt,
            _json(redacted),
            checksum,
            _resource_identity(event.resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_event')} "
            "(project_id, run_id, stage_id, event_id, event_type, schema_version, producer, "
            "observed_at, sequence, attempt, payload, payload_checksum, resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, producer, event_id) DO NOTHING",
            values,
        )
        inserted = cursor.rowcount == 1
        cursor.execute(
            f"SELECT project_id, run_id, event_type, schema_version, stage_id, observed_at, "
            f"sequence, attempt, payload_checksum, resource_identity FROM {self._table('run_event')} "
            f"WHERE project_id = {self.placeholder} AND producer = {self.placeholder} "
            f"AND event_id = {self.placeholder}",
            (event.project_id, event.producer, event.event_id),
        )
        existing = cursor.fetchone()
        if existing is None:
            raise RuntimeError("event insert did not return a durable record")
        if existing[0] != event.project_id or existing[1] != event.run_id:
            raise IdempotencyConflict(
                f"event {event.producer}/{event.event_id} is correlated to another run"
            )
        existing_resource_identity = existing[9]
        if isinstance(existing_resource_identity, dict):
            existing_resource_identity = _json(existing_resource_identity)
        if (
            existing[2],
            existing[3],
            existing[4],
            existing[7],
            existing[8],
            existing_resource_identity,
        ) != (
            event.event_type,
            event.schema_version,
            event.stage_id,
            event.attempt,
            checksum,
            _resource_identity(event.resource_ref),
        ):
            raise IdempotencyConflict(
                f"event {event.producer}/{event.event_id} was replayed with different attempt or payload"
            )
        if event.stage_id is not None:
            self._validate_existing_stage_attempt(
                cursor,
                project_id=event.project_id,
                run_id=event.run_id,
                stage_id=event.stage_id,
                attempt=event.attempt,
            )
        return inserted

    def _insert_reconciliation_decision(
        self, cursor: Any, decision: ReconciliationDecision
    ) -> tuple[ReconciliationDecision, bool]:
        missing = _json(list(decision.missing_evidence))
        record_checksum = payload_checksum(
            {
                "decision_id": decision.decision_id,
                "project_id": decision.project_id,
                "run_id": decision.run_id,
                "attempt": decision.attempt,
                "profile_id": decision.profile_id,
                "profile_version": decision.profile_version,
                "status": decision.status,
                "evidence_completeness": decision.evidence_completeness.value,
                "reason": decision.reason,
                "missing_evidence": decision.missing_evidence,
                "evidence_checksum": decision.evidence_checksum,
                "source": decision.source,
                "heartbeat_at": _timestamp(decision.heartbeat_at),
                "stale_after_seconds": decision.stale_after_seconds,
                "observed_event_count": decision.observed_event_count,
                "finished_at": _timestamp(decision.finished_at),
            }
        )
        values = (
            decision.decision_id,
            decision.project_id,
            decision.run_id,
            decision.attempt,
            decision.profile_id,
            decision.profile_version,
            decision.status,
            decision.evidence_completeness.value,
            _text(decision.reason),
            missing,
            decision.evidence_checksum,
            decision.observed_event_count,
            _text(decision.source),
            _timestamp(decision.heartbeat_at),
            decision.stale_after_seconds,
            _timestamp(decision.decided_at),
            _timestamp(decision.finished_at),
            record_checksum,
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_reconciliation_decision')} "
            "(decision_id, project_id, run_id, attempt, profile_id, profile_version, status, "
            "evidence_completeness, reason, missing_evidence, evidence_checksum, observed_event_count, "
            "source, heartbeat_at, stale_after_seconds, decided_at, finished_at, record_checksum) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, decision_id) DO NOTHING",
            values,
        )
        if cursor.rowcount != 1:
            cursor.execute(
                f"SELECT * FROM {self._table('run_reconciliation_decision')} "
                f"WHERE project_id = {self.placeholder} AND decision_id = {self.placeholder}",
                (decision.project_id, decision.decision_id),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise IdempotencyConflict(
                    f"reconciliation decision {decision.project_id}/{decision.decision_id} conflicted"
                )
            existing_row = self._row_dict(cursor, existing, table="run_reconciliation_decision")
            if existing_row["record_checksum"] != record_checksum:
                raise IdempotencyConflict(
                    f"reconciliation decision {decision.project_id}/{decision.decision_id} conflicted"
                )
            return self._decision_from_row(existing_row), False
        return decision, True

    @staticmethod
    def _decision_from_row(row: dict[str, Any]) -> ReconciliationDecision:
        missing_value = row["missing_evidence"]
        missing = json.loads(missing_value) if isinstance(missing_value, str) else missing_value
        return ReconciliationDecision(
            decision_id=row["decision_id"],
            project_id=row["project_id"],
            run_id=row["run_id"],
            attempt=int(row["attempt"]),
            profile_id=row["profile_id"],
            profile_version=row["profile_version"],
            status=row["status"],
            evidence_completeness=EvidenceCompleteness(row["evidence_completeness"]),
            reason=row["reason"],
            missing_evidence=tuple(missing),
            evidence_checksum=row["evidence_checksum"],
            observed_event_count=int(row["observed_event_count"]),
            source=row["source"],
            heartbeat_at=_parse_timestamp(row.get("heartbeat_at")),
            stale_after_seconds=(
                int(row["stale_after_seconds"])
                if row.get("stale_after_seconds") is not None
                else None
            ),
            decided_at=_parse_timestamp(row.get("decided_at")) or datetime.min.replace(tzinfo=UTC),
            finished_at=_parse_timestamp(row.get("finished_at")),
        )

    def _lock_run(self, cursor: Any, project_id: str, run_id: str) -> None:
        """Serialize child evidence writes with reconciliation on one parent run."""
        suffix = " FOR UPDATE" if self.placeholder != "?" else ""
        cursor.execute(
            f"SELECT project_id FROM {self._table('pipeline_run')} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder}{suffix}",
            (project_id, run_id),
        )
        if cursor.fetchone() is None:
            raise ValueError(f"run {project_id}/{run_id} does not exist")

    def _validate_event_stage_references(
        self,
        cursor: Any,
        event: RunEvent,
        stage: RunStage | None,
        quality_result: RunQualityResult | None,
    ) -> None:
        for stage_id in {
            stage_id
            for stage_id in (
                event.stage_id,
                quality_result.stage_id if quality_result is not None else None,
            )
            if stage_id is not None
        }:
            self._validate_existing_stage_attempt(
                cursor,
                project_id=event.project_id,
                run_id=event.run_id,
                stage_id=stage_id,
                attempt=event.attempt,
            )
        if stage is not None:
            self._validate_stage_write_references(cursor, stage)

    def _validate_existing_stage_attempt(
        self,
        cursor: Any,
        *,
        project_id: str,
        run_id: str,
        stage_id: str,
        attempt: int,
        error_type: type[ValueError] = ValueError,
    ) -> None:
        cursor.execute(
            f"SELECT attempt FROM {self._table('run_stage')} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
            f"AND stage_id = {self.placeholder}",
            (project_id, run_id, stage_id),
        )
        row = cursor.fetchone()
        if row is not None and row[0] != attempt:
            raise error_type(
                f"stage {project_id}/{run_id}/{stage_id} has attempt {row[0]}, expected {attempt}"
            )

    def _validate_stage_write_references(self, cursor: Any, stage: RunStage) -> None:
        self._validate_existing_stage_attempt(
            cursor,
            project_id=stage.project_id,
            run_id=stage.run_id,
            stage_id=stage.stage_id,
            attempt=stage.attempt,
            error_type=IdempotencyConflict,
        )
        for table in ("run_event", "run_quality_result"):
            cursor.execute(
                f"SELECT attempt FROM {self._table(table)} "
                f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
                f"AND stage_id = {self.placeholder}",
                (stage.project_id, stage.run_id, stage.stage_id),
            )
            mismatches = [row[0] for row in cursor.fetchall() if row[0] != stage.attempt]
            if mismatches:
                raise ValueError(
                    f"stage {stage.project_id}/{stage.run_id}/{stage.stage_id} attempt "
                    f"{stage.attempt} conflicts with {table} attempt {mismatches[0]}"
                )

    def _insert_stage(self, cursor: Any, stage: RunStage) -> None:
        self._require_id("stage_id", stage.stage_id)
        self._validate_stage_write_references(cursor, stage)
        immutable_payload: dict[str, Any] = {
            "stage_id": stage.stage_id,
            "project_id": stage.project_id,
            "run_id": stage.run_id,
            "stage_type": stage.stage_type,
            "provider": stage.provider,
            "tool": stage.tool,
            "asset": stage.asset,
            "attempt": stage.attempt,
        }
        if stage.resource_ref is not None:
            immutable_payload["resource_ref"] = asdict(stage.resource_ref)
        immutable_checksum = payload_checksum(immutable_payload)
        values = (
            stage.stage_id,
            stage.project_id,
            stage.run_id,
            stage.stage_type,
            _text(stage.provider),
            _text(stage.tool),
            _text(stage.asset),
            stage.attempt,
            stage.status,
            _timestamp(stage.started_at),
            _timestamp(stage.finished_at),
            _json(stage.metrics),
            _text(stage.error),
            immutable_checksum,
            _resource_identity(stage.resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_stage')} "
            "(stage_id, project_id, run_id, stage_type, provider, tool, asset, attempt, status, "
            "started_at, finished_at, metrics, error, record_checksum, resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id, stage_id) DO NOTHING",
            values,
        )
        if cursor.rowcount == 1:
            return
        cursor.execute(
            f"SELECT run_id, record_checksum FROM {self._table('run_stage')} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
            f"AND stage_id = {self.placeholder}",
            (stage.project_id, stage.run_id, stage.stage_id),
        )
        existing = cursor.fetchone()
        if existing is None or existing[0] != stage.run_id or existing[1] != immutable_checksum:
            raise IdempotencyConflict(
                f"stage {stage.project_id}/{stage.stage_id} was replayed differently"
            )
        p = self.placeholder
        cursor.execute(
            f"UPDATE {self._table('run_stage')} SET started_at = CASE "
            f"WHEN started_at IS NOT NULL THEN started_at "
            f"WHEN {p} IN ('running', 'started', 'start') THEN {p} "
            f"ELSE started_at END, status = CASE "
            f"WHEN status IN ('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
            f"THEN status ELSE {p} END, "
            f"finished_at = CASE WHEN status IN "
            f"('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
            f"THEN COALESCE(finished_at, {p}) ELSE COALESCE({p}, finished_at) END, "
            f"metrics = CASE WHEN status IN "
            f"('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
            f"THEN metrics ELSE {p} END, error = CASE WHEN status IN "
            f"('success', 'failed', 'error', 'cancelled', 'canceled', 'skipped', 'no_data', 'abandoned') "
            f"THEN COALESCE(error, {p}) ELSE COALESCE({p}, error) END "
            f"WHERE project_id = {p} AND run_id = {p} AND stage_id = {p}",
            (
                stage.status,
                _timestamp(stage.started_at),
                stage.status,
                _timestamp(stage.finished_at),
                _timestamp(stage.finished_at),
                _json(stage.metrics),
                _text(stage.error),
                _text(stage.error),
                stage.project_id,
                stage.run_id,
                stage.stage_id,
            ),
        )

    def _insert_resource(self, cursor: Any, resource: RunResource) -> None:
        self._require_id("resource_id", resource.resource_id)
        record_checksum = payload_checksum(_resource_checksum_payload(resource))
        values = (
            resource.resource_id,
            resource.project_id,
            resource.run_id,
            resource.attempt,
            resource.resource_kind,
            resource.role,
            _text(resource.normalized_identity),
            _text(resource.uri),
            _text(resource.table_name),
            _text(resource.catalog),
            _text(resource.ref_name),
            _text(resource.schema_hash),
            _text(resource.schema_hash_before),
            _text(resource.schema_hash_after),
            _text(resource.watermark),
            resource.record_count,
            resource.byte_count,
            _json(resource.staged_objects),
            _text(resource.snapshot_before),
            _text(resource.snapshot_after),
            _json(resource.metadata),
            record_checksum,
            _resource_identity(resource.resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_resource')} "
            "(resource_id, project_id, run_id, attempt, resource_kind, role, normalized_identity, uri, "
            "table_name, catalog, ref_name, schema_hash, schema_hash_before, schema_hash_after, "
            "watermark, record_count, byte_count, staged_objects, snapshot_before, snapshot_after, "
            "metadata, record_checksum, resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id, resource_id) DO NOTHING",
            values,
        )
        if cursor.rowcount != 1:
            self._confirm_immutable(
                cursor,
                table="run_resource",
                id_column="resource_id",
                project_id=resource.project_id,
                record_id=resource.resource_id,
                run_id=resource.run_id,
                checksum=record_checksum,
            )

    def _insert_lineage(self, cursor: Any, edge: RunLineageEdge) -> None:
        self._require_id("lineage_edge_id", edge.lineage_edge_id)
        record_checksum = payload_checksum(_lineage_checksum_payload(edge))
        values = (
            edge.lineage_edge_id,
            edge.project_id,
            edge.run_id,
            edge.attempt,
            _text(edge.source),
            _text(edge.target),
            _json(edge.column_mapping),
            _text(edge.origin),
            _text(edge.derivation),
            edge.confidence,
            record_checksum,
            _resource_identity(edge.source_resource_ref),
            _resource_identity(edge.target_resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_lineage_edge')} "
            "(lineage_edge_id, project_id, run_id, attempt, source, target, column_mapping, origin, "
            "derivation, confidence, record_checksum, source_resource_identity, target_resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id, lineage_edge_id) DO NOTHING",
            values,
        )
        if cursor.rowcount != 1:
            self._confirm_immutable(
                cursor,
                table="run_lineage_edge",
                id_column="lineage_edge_id",
                project_id=edge.project_id,
                record_id=edge.lineage_edge_id,
                run_id=edge.run_id,
                checksum=record_checksum,
            )

    def _insert_quality(self, cursor: Any, result: RunQualityResult) -> None:
        self._require_id("quality_result_id", result.quality_result_id)
        if result.stage_id is not None:
            self._validate_existing_stage_attempt(
                cursor,
                project_id=result.project_id,
                run_id=result.run_id,
                stage_id=result.stage_id,
                attempt=result.attempt,
            )
        record_checksum = payload_checksum(_quality_checksum_payload(result))
        values = (
            result.quality_result_id,
            result.project_id,
            result.run_id,
            result.attempt,
            result.stage_id,
            _text(result.check_id),
            _text(result.asset),
            _text(result.severity),
            result.blocking,
            result.passed,
            result.evaluated_count,
            result.failed_count,
            _text(result.failure_artifact_id),
            _json(result.metadata),
            record_checksum,
            _resource_identity(result.resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_quality_result')} "
            "(quality_result_id, project_id, run_id, attempt, stage_id, check_id, asset, severity, blocking, "
            "passed, evaluated_count, failed_count, failure_artifact_id, metadata, record_checksum, resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id, quality_result_id) DO NOTHING",
            values,
        )
        if cursor.rowcount != 1:
            self._confirm_immutable(
                cursor,
                table="run_quality_result",
                id_column="quality_result_id",
                project_id=result.project_id,
                record_id=result.quality_result_id,
                run_id=result.run_id,
                checksum=record_checksum,
            )

    def _insert_catalog_change(self, cursor: Any, change: RunCatalogChange) -> None:
        self._require_id("catalog_change_id", change.catalog_change_id)
        record_checksum = payload_checksum(_catalog_checksum_payload(change))
        values = (
            change.catalog_change_id,
            change.project_id,
            change.run_id,
            change.attempt,
            _text(change.catalog_ref),
            _text(change.content_key),
            _text(change.operation),
            _text(change.source_hash),
            _text(change.target_hash),
            _text(change.commit_hash),
            _text(change.commit_message),
            _text(change.merge_outcome),
            _text(change.snapshot_before),
            _text(change.snapshot_after),
            _text(change.quality_decision_id),
            _json(change.metadata),
            record_checksum,
            _resource_identity(change.resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_catalog_change')} "
            "(catalog_change_id, project_id, run_id, attempt, catalog_ref, content_key, operation, "
            "source_hash, target_hash, commit_hash, commit_message, merge_outcome, snapshot_before, "
            "snapshot_after, quality_decision_id, metadata, record_checksum, resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id, catalog_change_id) DO NOTHING",
            values,
        )
        if cursor.rowcount != 1:
            self._confirm_immutable(
                cursor,
                table="run_catalog_change",
                id_column="catalog_change_id",
                project_id=change.project_id,
                record_id=change.catalog_change_id,
                run_id=change.run_id,
                checksum=record_checksum,
            )

    def _insert_artifact(self, cursor: Any, artifact: RunArtifact) -> None:
        self._require_id("artifact_id", artifact.artifact_id)
        record_checksum = payload_checksum(_artifact_checksum_payload(artifact))
        values = (
            artifact.artifact_id,
            artifact.project_id,
            artifact.run_id,
            artifact.attempt,
            _text(artifact.artifact_kind),
            _text(artifact.uri),
            _text(artifact.content_type),
            _text(artifact.checksum),
            _text(artifact.retention_class),
            _timestamp(artifact.expires_at),
            artifact.legal_hold,
            artifact.status.value,
            record_checksum,
            _resource_identity(artifact.resource_ref),
        )
        cursor.execute(
            f"INSERT INTO {self._table('run_artifact')} "
            "(artifact_id, project_id, run_id, attempt, artifact_kind, uri, content_type, checksum, "
            "retention_class, expires_at, legal_hold, status, record_checksum, resource_identity) VALUES ("
            + ", ".join([self.placeholder] * len(values))
            + ") ON CONFLICT (project_id, run_id, artifact_id) DO NOTHING",
            values,
        )
        if cursor.rowcount != 1:
            self._confirm_immutable(
                cursor,
                table="run_artifact",
                id_column="artifact_id",
                project_id=artifact.project_id,
                record_id=artifact.artifact_id,
                run_id=artifact.run_id,
                checksum=record_checksum,
            )

    @staticmethod
    def _row_dict(cursor: Any, row: Any, *, table: str | None = None) -> dict[str, Any]:
        if hasattr(row, "keys"):
            result = dict(row)
        else:
            columns = [description[0] for description in cursor.description]
            result = dict(zip(columns, row, strict=True))
        if table is None:
            return result
        for field, expected_type in _JSON_ROW_FIELDS.get(table, {}).items():
            value = result.get(field)
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = None
            if value is None:
                value = [] if expected_type is list else {}
            if not isinstance(value, expected_type):
                raise ValueError(f"{table}.{field} must be a {expected_type.__name__}")
            result[field] = value
        for field in _OPTIONAL_JSON_ROW_FIELDS.get(table, set()):
            value = result.get(field)
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{table}.{field} must be JSON") from exc
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{table}.{field} must be an object or null")
            result[field] = value
        for field in _BOOLEAN_ROW_FIELDS.get(table, set()):
            if result.get(field) is not None:
                result[field] = bool(result[field])
        for field in _TIMESTAMP_ROW_FIELDS.get(table, set()):
            result[field] = _canonical_timestamp(result.get(field))
        return result

    @staticmethod
    def _require_id(name: str, value: str) -> None:
        if not value or not value.strip():
            raise ValueError(f"{name} must be a stable non-empty identifier")

    def _confirm_immutable(
        self,
        cursor: Any,
        *,
        table: str,
        id_column: str,
        project_id: str,
        record_id: str,
        run_id: str,
        checksum: str,
    ) -> None:
        cursor.execute(
            f"SELECT run_id, record_checksum FROM {self._table(table)} "
            f"WHERE project_id = {self.placeholder} AND run_id = {self.placeholder} "
            f"AND {id_column} = {self.placeholder}",
            (project_id, run_id, record_id),
        )
        existing = cursor.fetchone()
        if existing is None or existing[0] != run_id or existing[1] != checksum:
            raise IdempotencyConflict(
                f"{table} {project_id}/{record_id} was replayed with different evidence"
            )


class SQLiteRunEvidenceStore(_SqlRunEvidenceStore):
    """Local SQLite fallback for single-process development and tests."""

    placeholder = "?"

    def __init__(self, path: str | Path = ":memory:") -> None:
        super().__init__()
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")

    def _connect(self) -> sqlite3.Connection:
        return self._connection

    def _close_connection(self, connection: sqlite3.Connection) -> None:
        del connection

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._connection.close()

    def _initialize_schema(self) -> None:
        sql_root = Path(__file__).parent.parent / "sql"
        migrations = [
            (version, (sql_root / sqlite_name).read_text(encoding="utf-8"))
            for version, _, sqlite_name in _RUN_EVIDENCE_MIGRATIONS
        ]
        # executescript commits implicitly, so put BEGIN and the version write in
        # the same script. A killed process therefore leaves neither DDL nor a
        # misleading version marker behind.
        existing_table = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_evidence_schema_version'"
        ).fetchone()
        if existing_table:
            columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(run_evidence_schema_version)"
                ).fetchall()
            }
            if "checksum" not in columns:
                self._connection.execute(
                    "ALTER TABLE run_evidence_schema_version ADD COLUMN checksum TEXT"
                )
                self._connection.commit()
        rows = (
            self._connection.execute(
                "SELECT version, checksum FROM run_evidence_schema_version ORDER BY version"
            ).fetchall()
            if existing_table
            else []
        )
        known = {version: _migration_checksum(sql) for version, sql in migrations}
        for version, checksum in rows:
            if version not in known or version > RUN_EVIDENCE_SCHEMA_VERSION:
                raise RuntimeError(f"unsupported run-evidence schema version {version}")
            if checksum is not None and checksum != known[version]:
                raise RuntimeError(f"run-evidence migration checksum drift at version {version}")
        applied = {row[0] for row in rows}
        if applied and applied != set(range(1, max(applied) + 1)):
            raise RuntimeError("run-evidence schema has non-contiguous migration versions")
        # Old databases predate checksum tracking. Trust their contiguous version
        # markers once, then pin them to the shipped migration bytes.
        if rows:
            self._connection.executemany(
                "UPDATE run_evidence_schema_version SET checksum=? WHERE version=? AND checksum IS NULL",
                [(known[version], version) for version in applied],
            )
            self._connection.commit()
        for version, sql in migrations:
            if version in applied:
                continue
            checksum = known[version]
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql
                + "\nALTER TABLE run_evidence_schema_version ADD COLUMN checksum TEXT;\n"
                if version == 1
                else "BEGIN IMMEDIATE;\n" + sql
            )
            script += (
                "\nUPDATE run_evidence_schema_version SET checksum="
                f"'{checksum}' WHERE version={version};\nCOMMIT;"
            )
            try:
                self._connection.executescript(script)
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise


class PostgresRunEvidenceStore(_SqlRunEvidenceStore):
    """Production PostgreSQL adapter using the runtime psycopg2 dependency."""

    placeholder = "%s"
    table_prefix = "phlo."

    def __init__(self, dsn: str, *, connection_factory: Any | None = None) -> None:
        super().__init__()
        self.dsn = dsn
        self._connection_factory = connection_factory
        self._pool: Any | None = None

    def _connect(self) -> Any:
        if self._connection_factory is not None:
            return self._connection_factory()
        if self._pool is not None:
            return self._pool.getconn()
        try:
            from psycopg2.pool import ThreadedConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "PostgresRunEvidenceStore requires the runtime extra: install phlo[runtime]."
            ) from exc
        max_connections = int(os.environ.get("PHLO_RUN_EVIDENCE_POOL_MAX", "10"))
        self._pool = ThreadedConnectionPool(1, max(1, max_connections), self.dsn)
        return self._pool.getconn()

    def _close_connection(self, connection: Any) -> None:
        if self._connection_factory is not None:
            connection.close()
        elif self._pool is not None:
            self._pool.putconn(connection)
        else:
            connection.close()

    def close(self) -> None:
        """Close every pooled PostgreSQL connection."""
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None

    def _initialize_schema(self) -> None:
        sql_root = Path(__file__).parent.parent / "sql"
        connection = self._connect()
        try:
            for version, postgres_name, _ in _RUN_EVIDENCE_MIGRATIONS:
                sql = (sql_root / postgres_name).read_text(encoding="utf-8")
                checksum = _migration_checksum(sql)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT to_regclass('phlo.run_evidence_schema_version')")
                    exists = cursor.fetchone()[0] is not None
                    if exists:
                        cursor.execute(
                            "ALTER TABLE phlo.run_evidence_schema_version ADD COLUMN IF NOT EXISTS checksum TEXT"
                        )
                        cursor.execute(
                            "SELECT version, checksum FROM phlo.run_evidence_schema_version ORDER BY version"
                        )
                        rows = cursor.fetchall()
                        for found_version, found_checksum in rows:
                            expected = next(
                                (
                                    _migration_checksum(
                                        (sql_root / name).read_text(encoding="utf-8")
                                    )
                                    for candidate, name, _ in _RUN_EVIDENCE_MIGRATIONS
                                    if candidate == found_version
                                ),
                                None,
                            )
                            if expected is None or found_version > RUN_EVIDENCE_SCHEMA_VERSION:
                                raise RuntimeError(
                                    f"unsupported run-evidence schema version {found_version}"
                                )
                            if found_checksum is not None and found_checksum != expected:
                                raise RuntimeError(
                                    f"run-evidence migration checksum drift at version {found_version}"
                                )
                        applied = {row[0] for row in rows}
                        if applied and applied != set(range(1, max(applied) + 1)):
                            raise RuntimeError(
                                "run-evidence schema has non-contiguous migration versions"
                            )
                        if any(row[0] == version for row in rows):
                            cursor.execute(
                                "UPDATE phlo.run_evidence_schema_version SET checksum=%s "
                                "WHERE version=%s AND checksum IS NULL",
                                (checksum, version),
                            )
                            connection.commit()
                            continue
                    cursor.execute(sql)
                    cursor.execute(
                        "ALTER TABLE phlo.run_evidence_schema_version ADD COLUMN IF NOT EXISTS checksum TEXT"
                    )
                    cursor.execute(
                        "UPDATE phlo.run_evidence_schema_version SET checksum=%s WHERE version=%s",
                        (checksum, version),
                    )
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close_connection(connection)


def default_run_evidence_store() -> SQLiteRunEvidenceStore | PostgresRunEvidenceStore:
    """Resolve the production DSN or durable local path from environment."""
    dsn = os.environ.get("PHLO_RUN_EVIDENCE_DB_URL")
    if dsn:
        return PostgresRunEvidenceStore(dsn)
    environment = os.environ.get("PHLO_ENVIRONMENT", "dev").lower()
    if environment in {"prod", "production", "staging", "regulated"}:
        raise RuntimeError(
            "Run evidence requires PHLO_RUN_EVIDENCE_DB_URL in production, staging, "
            "and regulated environments; SQLite is local-only."
        )
    path = os.environ.get("PHLO_RUN_EVIDENCE_SQLITE_PATH", ".phlo/run-evidence.sqlite")
    return SQLiteRunEvidenceStore(path)
