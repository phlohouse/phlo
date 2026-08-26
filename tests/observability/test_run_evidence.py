"""Focused tests for the versioned run-evidence contract and sink.

Exercises append idempotency, redaction and checksums, project/producer
identity scoping, payload-conflict rollback, and report projections over a
SQLite-backed store.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from phlo.capabilities import ResourceRef
from phlo.hooks import HookBus
from phlo.hooks.emitters import (
    IngestionEventContext,
    IngestionEventEmitter,
    LineageEventContext,
    LineageEventEmitter,
    QualityResultEventContext,
    QualityResultEventEmitter,
)
from phlo.hooks.events import (
    HookCorrelation,
    IngestionEvent,
    LineageEvent,
    PublishEvent,
    QualityResultEvent,
    RunEvidenceObservationEvent,
    TransformEvent,
)
from phlo.run_evidence import (
    RUN_EVIDENCE_SCHEMA_VERSION,
    EvidenceCompleteness,
    IdempotencyConflict,
    PipelineRun,
    PostgresRunEvidenceStore,
    RunArtifact,
    RunCatalogChange,
    RunEvent,
    RunLineageEdge,
    RunQualityResult,
    RunResource,
    RunStage,
    SQLiteRunEvidenceStore,
    default_run_evidence_store,
)
from phlo.run_evidence.emit import emit_observation
from phlo.run_evidence.hooks import CoreRunEvidenceHookProvider
from phlo.run_evidence.redaction import canonical_json, payload_checksum
from phlo.run_evidence.report import ReportResourceIdentity, build_run_report
from phlo.run_evidence.store import (
    _artifact_checksum_payload,
    _catalog_checksum_payload,
    _lineage_checksum_payload,
    _quality_checksum_payload,
    _resource_checksum_payload,
)


def _store_with_run(*, project_id: str = "project", run_id: str = "run") -> SQLiteRunEvidenceStore:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id=project_id, run_id=run_id))
    return store


def _event(*, payload: dict, event_id: str = "event", producer: str = "producer") -> RunEvent:
    return RunEvent(
        project_id="project",
        run_id="run",
        event_id=event_id,
        event_type="ingestion.start",
        producer=producer,
        payload=payload,
    )


def _make_sqlite_v2_store(path: Path) -> SQLiteRunEvidenceStore:
    """Create a real 002+003 SQLite store without applying 004."""
    store = SQLiteRunEvidenceStore(path)
    sql_root = Path(__file__).parents[2] / "src" / "phlo" / "sql"
    for name in (
        "002_create_run_evidence_sqlite.sql",
        "003_reconcile_run_evidence_sqlite.sql",
    ):
        store._connection.executescript((sql_root / name).read_text(encoding="utf-8"))
    store._connection.commit()
    store._initialized = True
    return store


def test_run_report_is_attempt_scoped_and_marks_unproven_history_unavailable() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run", attempt=2))
    for attempt in (1, 2):
        started = datetime(2026, 1, attempt, tzinfo=UTC)
        store.append_stage(
            RunStage(
                project_id="project",
                run_id="run",
                stage_id=f"stage-{attempt}",
                stage_type="transform",
                attempt=attempt,
                started_at=started,
            )
        )
        store.append_resource(
            RunResource(
                project_id="project",
                run_id="run",
                resource_id=f"input-{attempt}",
                role="input",
                attempt=attempt,
            )
        )
        store.append_event(
            RunEvent(
                project_id="project",
                run_id="run",
                event_id=f"terminal-{attempt}",
                event_type="run.terminal",
                producer="test",
                payload={"status": "success", "attempt": attempt},
                sequence=2,
                attempt=attempt,
            )
        )

    report = build_run_report(store, "project", "run", 1)

    assert report.attempt == 1
    assert [stage.stage_id for stage in report.stages] == ["stage-1"]
    assert [event.event_id for event in report.lifecycle.events] == ["terminal-1"]
    assert report.lifecycle.events[0].producer == "test"
    assert [resource.resource_id for resource in report.inputs] == ["input-1"]
    assert report.terminal_outcome is not None
    assert report.terminal_outcome.status == "success"
    assert any(gap.field == "historical_fields" for gap in report.gaps)


def test_run_report_orders_cross_producer_events_and_redacts_legacy_locations() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(
            project_id="project",
            run_id="run",
            attempt=1,
            failure_summary="password=secret",
        )
    )
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    for producer in ("zeta", "alpha"):
        store.append_event(
            RunEvent(
                project_id="project",
                run_id="run",
                event_id="shared",
                event_type="stage.finished",
                producer=producer,
                payload={},
                observed_at=observed_at,
                sequence=1,
                attempt=1,
            )
        )
    store.append_resource(
        RunResource(
            project_id="project",
            run_id="run",
            resource_id="staged",
            role="staged",
            attempt=1,
            uri="s3://access_token=secret/data",
            staged_objects=[{"identity": "s3://client_secret=secret/staged", "checksum": "abc"}],
        )
    )
    store.append_artifact(
        RunArtifact(
            project_id="project",
            run_id="run",
            artifact_id="report",
            artifact_kind="report",
            attempt=1,
            uri="https://example.test/report?token=secret",
        )
    )

    rows = store.read_run_attempt("project", "run", 1)
    assert [event["producer"] for event in rows["events"]] == ["alpha", "zeta"]
    report = build_run_report(store, "project", "run", 1)
    assert [event.producer for event in report.lifecycle.events] == ["alpha", "zeta"]
    assert report.lifecycle.run is not None
    assert report.lifecycle.run.failure_summary == "password=<redacted>"
    assert report.staging[0].staged_objects == (
        {"identity": "s3://client_secret=<redacted>/staged", "checksum": "abc"},
    )
    assert report.staging[0].uri == "s3://access_token=<redacted>/data"
    assert report.artifacts[0].uri == "https://example.test/report?token=<redacted>"


def test_report_round_trips_canonical_resource_identities_without_display_inference() -> None:
    store = _store_with_run()
    run_ref = ResourceRef("run", "run", tenant="project")
    stage_ref = ResourceRef("asset", "raw.orders", tenant="project")
    resource_ref = ResourceRef(
        "dataset",
        "warehouse.orders",
        tenant="project",
        attributes={"classification": "restricted", "owner": "finance"},
    )
    quality_ref = ResourceRef("quality_check", "orders-freshness", tenant="project")
    catalog_ref = ResourceRef("catalog_change", "promotion-1", tenant="project")
    artifact_ref = ResourceRef("artifact", "report-1", tenant="project")
    store.append_event(
        RunEvent(
            project_id="project",
            run_id="run",
            event_id="event",
            event_type="stage.finished",
            producer="test",
            payload={},
            resource_ref=run_ref,
        ),
        stage=RunStage(
            project_id="project",
            run_id="run",
            stage_id="stage",
            asset="display-only-asset",
            resource_ref=stage_ref,
        ),
        resources=(
            RunResource(
                project_id="project",
                run_id="run",
                resource_id="resource",
                role="input",
                normalized_identity="display-only-resource",
                resource_ref=resource_ref,
            ),
        ),
        lineage_edges=(
            RunLineageEdge(
                project_id="project",
                run_id="run",
                lineage_edge_id="edge",
                source="display-source",
                target="display-target",
                source_resource_ref=stage_ref,
                target_resource_ref=resource_ref,
            ),
        ),
        quality_result=RunQualityResult(
            project_id="project",
            run_id="run",
            quality_result_id="quality",
            check_id="display-only-check",
            resource_ref=quality_ref,
        ),
        catalog_change=RunCatalogChange(
            project_id="project",
            run_id="run",
            catalog_change_id="catalog",
            operation="promotion",
            resource_ref=catalog_ref,
        ),
        artifacts=(
            RunArtifact(
                project_id="project",
                run_id="run",
                artifact_id="artifact",
                artifact_kind="log",
                resource_ref=artifact_ref,
            ),
        ),
    )

    report = build_run_report(store, "project", "run", 1)

    assert report.lifecycle.events[0].resource_identity == ReportResourceIdentity(
        "project", "run", "run", "project", {}
    )
    assert report.stages[0].resource_identity == ReportResourceIdentity(
        "project", "asset", "raw.orders", "project", {}
    )
    assert report.inputs[0].resource_identity == ReportResourceIdentity(
        "project",
        "dataset",
        "warehouse.orders",
        "project",
        {"classification": "restricted", "owner": "finance"},
    )
    assert report.lineage[0].source_resource_identity == ReportResourceIdentity(
        "project", "asset", "raw.orders", "project", {}
    )
    assert report.lineage[0].target_resource_identity == ReportResourceIdentity(
        "project",
        "dataset",
        "warehouse.orders",
        "project",
        {"classification": "restricted", "owner": "finance"},
    )
    assert report.quality[0].resource_identity == ReportResourceIdentity(
        "project", "quality_check", "orders-freshness", "project", {}
    )
    assert report.catalog_changes[0].resource_identity == ReportResourceIdentity(
        "project", "catalog_change", "promotion-1", "project", {}
    )
    assert report.artifacts[0].resource_identity == ReportResourceIdentity(
        "project", "artifact", "report-1", "project", {}
    )
    assert not any(gap.field == "resource_identities" for gap in report.gaps)


def _insert_v2_fixture_rows(store: object) -> dict[str, object]:
    """Insert rows using only the columns available after migrations 002+003."""
    project_id = "legacy-project"
    run_id = "legacy-run"
    attempt = 2
    observed_at = datetime(2026, 7, 1, tzinfo=UTC).isoformat()
    resource = RunResource(
        project_id=project_id,
        run_id=run_id,
        resource_id="legacy-resource",
        attempt=attempt,
        resource_kind="staged_object",
        role="staged",
        table_name="raw.events",
        staged_objects=[{"identity": "staged/events.parquet", "checksum": "abc"}],
    )
    catalog = RunCatalogChange(
        project_id=project_id,
        run_id=run_id,
        catalog_change_id="legacy-catalog",
        attempt=attempt,
        operation="promotion",
        catalog_ref="main",
        metadata={"legacy": True},
    )
    quality = RunQualityResult(
        project_id=project_id,
        run_id=run_id,
        quality_result_id="legacy-quality",
        attempt=attempt,
        check_id="legacy-check",
        blocking=True,
        passed=True,
        metadata={"legacy": True},
    )
    artifact = RunArtifact(
        project_id=project_id,
        run_id=run_id,
        artifact_id="legacy-artifact",
        attempt=attempt,
        artifact_kind="report",
        expires_at=datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC),
        legal_hold=True,
    )
    event_payload = {"legacy": True}
    event = RunEvent(
        project_id=project_id,
        run_id=run_id,
        event_id="legacy-event",
        event_type="ingestion.end",
        producer="legacy",
        payload=event_payload,
        observed_at=datetime.fromisoformat(observed_at),
        attempt=attempt,
    )
    store.append_pipeline_run(
        PipelineRun(
            project_id=project_id,
            run_id=run_id,
            attempt=attempt,
            status="success",
            started_at=datetime.fromisoformat(observed_at),
            finished_at=datetime.fromisoformat(observed_at),
        )
    )
    stage = RunStage(
        project_id=project_id,
        run_id=run_id,
        stage_id="legacy-stage",
        stage_type="ingestion",
        attempt=attempt,
        status="success",
        started_at=datetime(2026, 7, 1, 1, 2, 3, tzinfo=UTC),
        finished_at=datetime(2026, 7, 1, 1, 3, 4, tzinfo=UTC),
    )
    with store._transaction() as (_, cursor):
        p = store.placeholder
        stage_checksum = payload_checksum(
            {
                "stage_id": stage.stage_id,
                "project_id": project_id,
                "run_id": run_id,
                "stage_type": stage.stage_type,
                "provider": stage.provider,
                "tool": stage.tool,
                "asset": stage.asset,
                "attempt": attempt,
            }
        )
        cursor.execute(
            f"INSERT INTO {store._table('run_stage')} "
            "(stage_id, project_id, run_id, stage_type, attempt, status, started_at, finished_at, "
            "record_checksum) VALUES (" + ", ".join([p] * 9) + ")",
            (
                stage.stage_id,
                project_id,
                run_id,
                stage.stage_type,
                attempt,
                stage.status,
                stage.started_at.isoformat(),
                stage.finished_at.isoformat(),
                stage_checksum,
            ),
        )
        cursor.execute(
            f"INSERT INTO {store._table('run_event')} "
            "(project_id, run_id, event_id, event_type, schema_version, producer, observed_at, "
            "attempt, payload, payload_checksum) VALUES (" + ", ".join([p] * 10) + ")",
            (
                project_id,
                run_id,
                event.event_id,
                event.event_type,
                event.schema_version,
                event.producer,
                observed_at,
                attempt,
                canonical_json(event_payload),
                payload_checksum(event_payload),
            ),
        )
        resource_checksum = payload_checksum(_resource_checksum_payload(resource))
        cursor.execute(
            f"INSERT INTO {store._table('run_resource')} "
            "(resource_id, project_id, run_id, attempt, resource_kind, role, table_name, "
            "staged_objects, record_checksum) VALUES (" + ", ".join([p] * 9) + ")",
            (
                resource.resource_id,
                project_id,
                run_id,
                attempt,
                resource.resource_kind,
                resource.role,
                resource.table_name,
                canonical_json(resource.staged_objects),
                resource_checksum,
            ),
        )
        catalog_checksum = payload_checksum(_catalog_checksum_payload(catalog))
        cursor.execute(
            f"INSERT INTO {store._table('run_catalog_change')} "
            "(catalog_change_id, project_id, run_id, attempt, catalog_ref, operation, metadata, "
            "record_checksum) VALUES (" + ", ".join([p] * 8) + ")",
            (
                catalog.catalog_change_id,
                project_id,
                run_id,
                attempt,
                catalog.catalog_ref,
                catalog.operation,
                canonical_json(catalog.metadata),
                catalog_checksum,
            ),
        )
        quality_checksum = payload_checksum(_quality_checksum_payload(quality))
        cursor.execute(
            f"INSERT INTO {store._table('run_quality_result')} "
            "(quality_result_id, project_id, run_id, attempt, check_id, blocking, passed, metadata, "
            "record_checksum) VALUES (" + ", ".join([p] * 9) + ")",
            (
                quality.quality_result_id,
                project_id,
                run_id,
                attempt,
                quality.check_id,
                quality.blocking,
                quality.passed,
                canonical_json(quality.metadata),
                quality_checksum,
            ),
        )
        artifact_checksum = payload_checksum(_artifact_checksum_payload(artifact))
        cursor.execute(
            f"INSERT INTO {store._table('run_artifact')} "
            "(artifact_id, project_id, run_id, attempt, artifact_kind, expires_at, legal_hold, status, "
            "record_checksum) VALUES (" + ", ".join([p] * 9) + ")",
            (
                artifact.artifact_id,
                project_id,
                run_id,
                attempt,
                artifact.artifact_kind,
                artifact.expires_at.isoformat() if artifact.expires_at else None,
                artifact.legal_hold,
                artifact.status.value,
                artifact_checksum,
            ),
        )
    return {
        "project_id": project_id,
        "run_id": run_id,
        "attempt": attempt,
        "event": event,
        "resource": resource,
        "catalog": catalog,
        "quality": quality,
        "artifact": artifact,
        "stage": stage,
    }


def _assert_public_record_shapes(store: object, fixture: dict[str, object]) -> None:
    project_id = fixture["project_id"]
    run_id = fixture["run_id"]
    attempt = fixture["attempt"]
    resource = store.list_resources(project_id, run_id, attempt=attempt)[0]
    catalog = store.list_catalog_changes(project_id, run_id, attempt=attempt)[0]
    quality = store.list_quality_results(project_id, run_id, attempt=attempt)[0]
    artifact = store.list_artifacts(project_id, run_id, attempt=attempt)[0]
    assert resource["staged_objects"] == [{"identity": "staged/events.parquet", "checksum": "abc"}]
    assert isinstance(resource["metadata"], dict)
    assert isinstance(catalog["metadata"], dict)
    assert quality["blocking"] is True
    assert quality["passed"] is True
    assert artifact["legal_hold"] is True
    assert artifact["expires_at"] == "2026-07-15T01:02:03+00:00"


def _timestamp_projection(store: object, fixture: dict[str, object]) -> dict[str, object]:
    """Select explicit timestamp fields whose values must match across drivers."""
    project_id = fixture["project_id"]
    run_id = fixture["run_id"]
    attempt = fixture["attempt"]
    with store._transaction() as (_, cursor):
        cursor.execute(
            f"SELECT * FROM {store._table('run_stage')} "
            f"WHERE project_id = {store.placeholder} AND run_id = {store.placeholder} "
            f"AND attempt = {store.placeholder}",
            (project_id, run_id, attempt),
        )
        stage = store._row_dict(cursor, cursor.fetchone(), table="run_stage")
    run = store.get_run(project_id, run_id)
    event = store.list_events(project_id, run_id)[0]
    artifact = store.list_artifacts(project_id, run_id, attempt=attempt)[0]
    return {
        "run": {field: run[field] for field in ("started_at", "finished_at")},
        "event": {field: event[field] for field in ("observed_at",)},
        "stage": {field: stage[field] for field in ("started_at", "finished_at")},
        "artifact": {field: artifact[field] for field in ("expires_at",)},
        "nullable": {
            "run_finished_at": None if run["finished_at"] is None else run["finished_at"],
            "stage_started_at": stage["started_at"],
            "artifact_expires_at": artifact["expires_at"],
        },
    }


def _assert_nullable_public_timestamps(store: object, fixture: dict[str, object]) -> None:
    project_id = fixture["project_id"]
    run_id = fixture["run_id"]
    with store._transaction() as (_, cursor):
        for table, field in (
            ("pipeline_run", "finished_at"),
            ("run_stage", "started_at"),
            ("run_artifact", "expires_at"),
        ):
            cursor.execute(
                f"UPDATE {store._table(table)} SET {field} = NULL "
                f"WHERE project_id = {store.placeholder} AND run_id = {store.placeholder}",
                (project_id, run_id),
            )
    assert store.get_run(project_id, run_id)["finished_at"] is None
    assert (
        store.list_artifacts(project_id, run_id, attempt=fixture["attempt"])[0]["expires_at"]
        is None
    )
    with store._transaction() as (_, cursor):
        cursor.execute(
            f"SELECT * FROM {store._table('run_stage')} "
            f"WHERE project_id = {store.placeholder} AND run_id = {store.placeholder}",
            (project_id, run_id),
        )
        assert store._row_dict(cursor, cursor.fetchone(), table="run_stage")["started_at"] is None


def _fixture_checksums(store: object) -> dict[str, str]:
    checksums: dict[str, str] = {}
    with store._transaction() as (_, cursor):
        for table in ("run_resource", "run_catalog_change", "run_quality_result", "run_artifact"):
            cursor.execute(f"SELECT record_checksum FROM {store._table(table)}")
            checksums[table] = cursor.fetchone()[0]
    return checksums


def test_event_append_is_redacted_checksumed_and_idempotent() -> None:
    store = _store_with_run()
    event = _event(payload={"nested": {"token": "secret", "value": 1}})

    assert store.append_event(event) is True
    replay = replace(event, payload={"nested": {"value": 1, "token": "another-secret"}})
    assert store.append_event(replay) is False

    row = store.list_events("project", "run")[0]
    assert "secret" not in row["payload"]
    assert row["payload"]["nested"]["token"] == "<redacted>"
    assert row["schema_version"] == "1.0"


def test_event_identity_is_scoped_by_project_and_producer() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="one", run_id="same"))
    store.append_pipeline_run(PipelineRun(project_id="two", run_id="same"))
    first = replace(_event(payload={"value": 1}), project_id="one", run_id="same")

    assert store.append_event(first, run=PipelineRun(project_id="one", run_id="same")) is True
    assert (
        store.append_event(
            replace(first, project_id="two", run_id="same"),
            run=PipelineRun(project_id="two", run_id="same"),
        )
        is True
    )
    assert store.append_event(replace(first, producer="other")) is True


def test_event_payload_conflict_rolls_back_and_preserves_original() -> None:
    store = _store_with_run()
    event = _event(payload={"value": 1})
    store.append_event(event)

    with pytest.raises(IdempotencyConflict):
        store.append_event(replace(event, payload={"value": 2}))

    assert store.list_events("project", "run")[0]["payload"] == {"value": 1}


@pytest.mark.parametrize(
    ("object_name", "boundary"),
    [
        (object_name, boundary)
        for object_name in ("run", "stage", "quality_result", "lineage_edge")
        for boundary in ("project", "run")
    ],
)
def test_append_event_rejects_cross_boundary_optional_objects_before_mutation(
    object_name: str,
    boundary: str,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    event = _event(payload={"value": 1})
    objects = {
        "run": PipelineRun(project_id="project", run_id="run"),
        "stage": RunStage(project_id="project", run_id="run", stage_id="stage"),
        "quality_result": RunQualityResult(
            project_id="project", run_id="run", quality_result_id="quality", check_id="check"
        ),
        "lineage_edge": RunLineageEdge(
            project_id="project", run_id="run", lineage_edge_id="lineage", source="a", target="b"
        ),
    }
    replacement = "other-project" if boundary == "project" else "other-run"
    value = objects[object_name]
    invalid = (
        replace(value, project_id=replacement)
        if boundary == "project"
        else replace(value, run_id=replacement)
    )
    kwargs = (
        {"lineage_edges": (invalid,)} if object_name == "lineage_edge" else {object_name: invalid}
    )

    with pytest.raises(ValueError, match="project/run boundaries"):
        store.append_event(event, **kwargs)

    assert store.get_run("project", "run") is None
    assert store.count_events("project", "run") == 0


@pytest.mark.parametrize("object_name", ["run", "stage", "quality_result"])
def test_append_event_rejects_cross_attempt_optional_objects_before_mutation(
    object_name: str,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    event = replace(_event(payload={"value": 1}), attempt=1)
    objects = {
        "run": PipelineRun(project_id="project", run_id="run", attempt=1),
        "stage": RunStage(project_id="project", run_id="run", stage_id="stage", attempt=1),
        "quality_result": RunQualityResult(
            project_id="project",
            run_id="run",
            quality_result_id="quality",
            check_id="check",
            attempt=1,
        ),
    }
    invalid = replace(objects[object_name], attempt=2)

    with pytest.raises(ValueError, match="attempt boundaries"):
        store.append_event(event, **{object_name: invalid})

    assert store.get_run("project", "run") is None
    assert store.count_events("project", "run") == 0


def test_append_event_preflights_the_entire_bundle_before_valid_prefix_mutation() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    event = _event(payload={"value": 1})
    run = PipelineRun(project_id="project", run_id="run")
    stage = RunStage(project_id="project", run_id="run", stage_id="stage")
    quality = RunQualityResult(
        project_id="project", run_id="run", quality_result_id="quality", check_id="check"
    )
    valid_edge = RunLineageEdge(
        project_id="project", run_id="run", lineage_edge_id="valid", source="a", target="b"
    )
    invalid_edge = replace(valid_edge, project_id="other-project", lineage_edge_id="invalid")

    with pytest.raises(ValueError, match="project/run boundaries"):
        store.append_event(
            event,
            run=run,
            stage=stage,
            quality_result=quality,
            lineage_edges=(valid_edge, invalid_edge),
        )

    with store._transaction() as (_, cursor):
        for table in (
            "pipeline_run",
            "run_event",
            "run_stage",
            "run_quality_result",
            "run_lineage_edge",
        ):
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            assert cursor.fetchone()[0] == 0, table


@pytest.mark.parametrize("conflict", ["event_stage", "event_quality", "stage_quality"])
def test_append_event_rejects_conflicting_stage_identities_before_mutation(
    conflict: str,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    event = replace(_event(payload={"value": 1}), stage_id="event-stage")
    stage = RunStage(project_id="project", run_id="run", stage_id="provided-stage")
    quality = RunQualityResult(
        project_id="project",
        run_id="run",
        quality_result_id="quality",
        check_id="check",
        stage_id="quality-stage",
    )
    if conflict == "event_stage":
        quality = replace(quality, stage_id=None)
    elif conflict == "event_quality":
        stage = None
    else:
        event = replace(event, stage_id=None)

    with pytest.raises(ValueError, match="conflicting stage identities"):
        store.append_event(event, stage=stage, quality_result=quality)

    assert store.get_run("project", "run") is None
    assert store.count_events("project", "run") == 0


def test_append_event_rejects_existing_stage_from_another_attempt_before_mutation() -> None:
    store = _store_with_run()
    store.append_stage(RunStage(project_id="project", run_id="run", stage_id="stage", attempt=1))
    event = replace(_event(payload={"value": 1}), stage_id="stage", attempt=2)
    quality = RunQualityResult(
        project_id="project",
        run_id="run",
        quality_result_id="quality-attempt-2",
        check_id="check",
        stage_id="stage",
        attempt=2,
    )

    with pytest.raises(ValueError, match="stage .* has attempt 1, expected 2"):
        store.append_event(
            event,
            run=PipelineRun(project_id="project", run_id="run", attempt=2),
            quality_result=quality,
        )

    assert store.count_events("project", "run") == 0
    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT attempt FROM run_stage WHERE project_id = ? AND run_id = ? AND stage_id = ?",
            ("project", "run", "stage"),
        )
        assert cursor.fetchone()[0] == 1


def test_append_stage_rejects_existing_event_from_another_attempt() -> None:
    store = _store_with_run()
    store.append_event(replace(_event(payload={"value": 1}), stage_id="stage", attempt=1))

    with pytest.raises(ValueError, match="conflicts with run_event attempt 1"):
        store.append_stage(
            RunStage(project_id="project", run_id="run", stage_id="stage", attempt=2)
        )

    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT COUNT(*) FROM run_stage WHERE project_id = ? AND run_id = ? AND stage_id = ?",
            ("project", "run", "stage"),
        )
        assert cursor.fetchone()[0] == 0


def test_event_before_stage_same_attempt_remains_allowed() -> None:
    store = _store_with_run()
    store.append_event(replace(_event(payload={"value": 1}), stage_id="stage", attempt=1))

    store.append_stage(RunStage(project_id="project", run_id="run", stage_id="stage", attempt=1))

    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT attempt FROM run_stage WHERE project_id = ? AND run_id = ? AND stage_id = ?",
            ("project", "run", "stage"),
        )
        assert cursor.fetchone()[0] == 1


def test_append_quality_rejects_existing_stage_from_another_attempt() -> None:
    store = _store_with_run()
    store.append_stage(RunStage(project_id="project", run_id="run", stage_id="stage", attempt=1))

    with pytest.raises(ValueError, match="has attempt 1, expected 2"):
        store.append_quality_result(
            RunQualityResult(
                project_id="project",
                run_id="run",
                quality_result_id="quality-attempt-2",
                check_id="check",
                stage_id="stage",
                attempt=2,
            )
        )

    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT COUNT(*) FROM run_quality_result WHERE project_id = ? AND run_id = ?",
            ("project", "run"),
        )
        assert cursor.fetchone()[0] == 0


def test_changed_run_upsert_advances_updated_at() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    initial = PipelineRun(project_id="project", run_id="run", trigger="initial")
    store.append_pipeline_run(initial)

    # updated_at is stamped by SQLite CURRENT_TIMESTAMP (one-second resolution);
    # pin the first write into the past so no wall-clock sleep is needed.
    with store._transaction() as (_, cursor):
        cursor.execute(
            "UPDATE pipeline_run SET updated_at = ? WHERE project_id = ? AND run_id = ?",
            ("2000-01-01 00:00:00", "project", "run"),
        )
    before = store.get_run("project", "run")

    store.append_pipeline_run(replace(initial, status="failed", trigger="changed"))
    after = store.get_run("project", "run")

    assert before is not None and after is not None
    assert after["status"] == "failed"
    assert datetime.fromisoformat(after["updated_at"]) > datetime.fromisoformat(
        before["updated_at"]
    )


def _assert_concurrent_duplicate_replay(
    store_factory: object,
    *,
    project_id: str,
    run_id: str,
) -> None:
    event = RunEvent(
        project_id=project_id,
        run_id=run_id,
        event_id="concurrent-event",
        event_type="run.start",
        producer="test",
        payload={"same": True},
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    running = PipelineRun(
        project_id=project_id,
        run_id=run_id,
        status="running",
        trigger="running-owner",
        started_at=event.observed_at,
    )
    failed = replace(running, status="failed", trigger="losing-replay")
    start = threading.Barrier(2)

    def append(run: PipelineRun) -> bool:
        store = store_factory()
        start.wait()
        return store.append_event(event, run=run)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, (running, failed)))

    assert sorted(results) == [False, True]
    winner = running if results[0] else failed
    store = store_factory()
    final_run = store.get_run(project_id, run_id)
    assert final_run is not None
    assert final_run["status"] == winner.status
    assert final_run["trigger"] == winner.trigger
    assert store.count_events(project_id, run_id) == 1


def test_concurrent_duplicate_replay_does_not_apply_loser_run_metadata(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent-run-evidence.sqlite"

    def factory() -> SQLiteRunEvidenceStore:
        return SQLiteRunEvidenceStore(database)

    store = factory()
    store._ensure_schema()
    _assert_concurrent_duplicate_replay(
        factory,
        project_id="project",
        run_id="concurrent-run",
    )


def test_postgres_concurrent_duplicate_replay_does_not_apply_loser_run_metadata() -> None:
    dsn = os.environ.get("PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN for the live PostgreSQL race test")

    project_id = f"concurrent-project-{uuid4().hex}"
    run_id = f"concurrent-run-{uuid4().hex}"
    store = PostgresRunEvidenceStore(dsn)
    store._ensure_schema()

    entered = threading.Event()
    release = threading.Event()

    # GatedStore pins the race window: the owner thread blocks inside its open
    # transaction at the post-event parent-run update, so the loser's duplicate
    # replay is guaranteed to commit while the owner's transaction is still
    # pending. Without the gate the interleaving depends on thread timing.
    class GatedStore(PostgresRunEvidenceStore):
        def _upsert_run(self, cursor: object, run: PipelineRun) -> None:
            super()._upsert_run(cursor, run)
            if run.status == "running":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("timed out waiting to release the owning event write")

    def factory() -> GatedStore:
        return GatedStore(dsn)

    event = RunEvent(
        project_id=project_id,
        run_id=run_id,
        event_id="concurrent-event",
        event_type="run.start",
        producer="test",
        payload={"same": True},
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    running = PipelineRun(
        project_id=project_id,
        run_id=run_id,
        status="running",
        trigger="running-owner",
        started_at=event.observed_at,
    )
    failed = replace(running, status="failed", trigger="losing-replay")

    def append(run: PipelineRun) -> bool:
        return factory().append_event(event, run=run)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            owner = executor.submit(append, running)
            assert entered.wait(10), "owner did not reach the post-event parent update"
            loser = executor.submit(append, failed)
            release.set()
            results = [owner.result(), loser.result()]

        assert results == [True, False]
        final_run = store.get_run(project_id, run_id)
        assert final_run is not None
        assert final_run["status"] == "running"
        assert final_run["trigger"] == "running-owner"
        assert store.count_events(project_id, run_id) == 1
    finally:
        with store._transaction() as (_, cursor):
            cursor.execute(
                "DELETE FROM phlo.run_event WHERE project_id = %s AND run_id = %s",
                (project_id, run_id),
            )
            cursor.execute(
                "DELETE FROM phlo.pipeline_run WHERE project_id = %s AND run_id = %s",
                (project_id, run_id),
            )


def test_event_and_derived_rows_share_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store_with_run()
    stage = RunStage(project_id="project", run_id="run", stage_id="stage", stage_type="ingest")

    def fail_after_event(*_args, **_kwargs) -> None:
        raise RuntimeError("failpoint")

    monkeypatch.setattr(store, "_insert_stage", fail_after_event)
    with pytest.raises(RuntimeError, match="failpoint"):
        store.append_event(_event(payload={"value": 1}), stage=stage)

    assert store.count_events("project", "run") == 0
    monkeypatch.setattr(store, "_insert_stage", SQLiteRunEvidenceStore._insert_stage.__get__(store))
    assert store.append_event(_event(payload={"value": 1}), stage=stage) is True


@pytest.mark.parametrize(
    ("family", "first", "different"),
    [
        (
            "stage",
            RunStage(project_id="project", run_id="run", stage_id="stable", stage_type="ingest"),
            RunStage(project_id="project", run_id="run", stage_id="stable", stage_type="transform"),
        ),
        (
            "resource",
            RunResource(project_id="project", run_id="run", resource_id="stable", uri="a"),
            RunResource(project_id="project", run_id="run", resource_id="stable", uri="b"),
        ),
        (
            "lineage",
            RunLineageEdge(
                project_id="project", run_id="run", lineage_edge_id="stable", source="a", target="b"
            ),
            RunLineageEdge(
                project_id="project", run_id="run", lineage_edge_id="stable", source="a", target="c"
            ),
        ),
        (
            "quality",
            RunQualityResult(
                project_id="project",
                run_id="run",
                quality_result_id="stable",
                check_id="check",
                passed=True,
            ),
            RunQualityResult(
                project_id="project",
                run_id="run",
                quality_result_id="stable",
                check_id="other",
                passed=True,
            ),
        ),
        (
            "catalog",
            RunCatalogChange(
                project_id="project", run_id="run", catalog_change_id="stable", operation="commit"
            ),
            RunCatalogChange(
                project_id="project", run_id="run", catalog_change_id="stable", operation="merge"
            ),
        ),
        (
            "artifact",
            RunArtifact(
                project_id="project", run_id="run", artifact_id="stable", artifact_kind="log"
            ),
            RunArtifact(
                project_id="project", run_id="run", artifact_id="stable", artifact_kind="manifest"
            ),
        ),
    ],
)
def test_normalized_records_are_idempotent_and_conflict_on_content(
    family, first, different
) -> None:
    store = _store_with_run()
    append = getattr(
        store,
        f"append_{'lineage_edge' if family == 'lineage' else 'quality_result' if family == 'quality' else 'catalog_change' if family == 'catalog' else family}",
    )

    append(first)
    append(first)
    with pytest.raises(IdempotencyConflict):
        append(different)


def test_child_ids_are_project_scoped_and_cross_run_quality_refs_fail() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="one"))
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="two"))
    store.append_pipeline_run(PipelineRun(project_id="other", run_id="one"))
    store.append_stage(
        RunStage(project_id="project", run_id="one", stage_id="same", stage_type="ingest")
    )
    store.append_stage(
        RunStage(project_id="other", run_id="one", stage_id="same", stage_type="ingest")
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.append_quality_result(
            RunQualityResult(
                project_id="project",
                run_id="two",
                quality_result_id="quality",
                check_id="check",
                stage_id="same",
            )
        )
    store.append_artifact(
        RunArtifact(project_id="project", run_id="one", artifact_id="artifact", artifact_kind="log")
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.append_quality_result(
            RunQualityResult(
                project_id="project",
                run_id="two",
                quality_result_id="quality-artifact",
                check_id="check",
                failure_artifact_id="artifact",
            )
        )


def test_same_project_child_ids_are_scoped_to_each_run() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    for run_id in ("one", "two"):
        store.append_pipeline_run(PipelineRun(project_id="project", run_id=run_id))

    store.append_stage(
        RunStage(project_id="project", run_id="one", stage_id="same", stage_type="ingest")
    )
    store.append_stage(
        RunStage(project_id="project", run_id="two", stage_id="same", stage_type="ingest")
    )
    store.append_resource(
        RunResource(project_id="project", run_id="one", resource_id="same", uri="one")
    )
    store.append_resource(
        RunResource(project_id="project", run_id="two", resource_id="same", uri="two")
    )
    store.append_lineage_edge(
        RunLineageEdge(
            project_id="project", run_id="one", lineage_edge_id="same", source="a", target="b"
        )
    )
    store.append_lineage_edge(
        RunLineageEdge(
            project_id="project", run_id="two", lineage_edge_id="same", source="a", target="c"
        )
    )
    store.append_quality_result(
        RunQualityResult(
            project_id="project", run_id="one", quality_result_id="same", check_id="one"
        )
    )
    store.append_quality_result(
        RunQualityResult(
            project_id="project", run_id="two", quality_result_id="same", check_id="two"
        )
    )
    store.append_catalog_change(
        RunCatalogChange(
            project_id="project", run_id="one", catalog_change_id="same", operation="commit"
        )
    )
    store.append_catalog_change(
        RunCatalogChange(
            project_id="project", run_id="two", catalog_change_id="same", operation="merge"
        )
    )
    store.append_artifact(
        RunArtifact(project_id="project", run_id="one", artifact_id="same", artifact_kind="log")
    )
    store.append_artifact(
        RunArtifact(
            project_id="project", run_id="two", artifact_id="same", artifact_kind="manifest"
        )
    )

    with store._transaction() as (_, cursor):
        for table in (
            "run_stage",
            "run_resource",
            "run_lineage_edge",
            "run_quality_result",
            "run_catalog_change",
            "run_artifact",
        ):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", ("project",))
            assert cursor.fetchone()[0] == 2, table


def test_child_primary_keys_include_project_and_run() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    expected = {
        "run_stage": ["project_id", "run_id", "stage_id"],
        "run_resource": ["project_id", "run_id", "resource_id"],
        "run_lineage_edge": ["project_id", "run_id", "lineage_edge_id"],
        "run_quality_result": ["project_id", "run_id", "quality_result_id"],
        "run_catalog_change": ["project_id", "run_id", "catalog_change_id"],
        "run_artifact": ["project_id", "run_id", "artifact_id"],
    }
    with store._transaction() as (_, cursor):
        for table, columns in expected.items():
            cursor.execute(f"PRAGMA table_info({table})")
            primary_key = [
                row[1] for row in sorted(cursor.fetchall(), key=lambda row: row[5]) if row[5]
            ]
            assert primary_key == columns, table


def test_run_attempt_and_terminal_status_are_monotonic() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", attempt=1, status="failed")
    )
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", attempt=2, status="running")
    )
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", attempt=2, status="success")
    )

    row = store.get_run("project", "run")
    assert row["attempt"] == 2
    assert row["status"] == "success"

    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", attempt=1, status="failed")
    )
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", attempt=2, status="failed")
    )

    row = store.get_run("project", "run")
    assert row["attempt"] == 2
    assert row["status"] == "success"


def test_higher_attempt_replaces_summary_and_late_attempt_is_ignored() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    first_finished = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    second_finished = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
    late_first_finished = datetime(2026, 7, 13, 12, 2, tzinfo=UTC)
    first = PipelineRun(
        project_id="project",
        run_id="run",
        attempt=1,
        trace_id="trace-one",
        effective_identity="identity-one",
        code_version="code-one",
        config_version="config-one",
        status="failed",
        finished_at=first_finished,
        failure_summary="first failure",
        evidence_completeness=EvidenceCompleteness.COMPLETE,
    )
    second_running = replace(
        first,
        attempt=2,
        trace_id="trace-two",
        effective_identity="identity-two",
        code_version="code-two",
        config_version="config-two",
        status="running",
        finished_at=None,
        failure_summary=None,
        evidence_completeness=EvidenceCompleteness.INCOMPLETE,
    )
    store.append_pipeline_run(first)
    store.append_pipeline_run(second_running)

    row = store.get_run("project", "run")
    assert (
        row["attempt"],
        row["status"],
        row["trace_id"],
        row["effective_identity"],
        row["code_version"],
        row["config_version"],
    ) == (2, "running", "trace-two", "identity-two", "code-two", "config-two")
    assert row["finished_at"] is None
    assert row["failure_summary"] is None
    assert row["evidence_completeness"] == EvidenceCompleteness.INCOMPLETE

    store.append_pipeline_run(
        replace(
            second_running,
            status="success",
            finished_at=second_finished,
            evidence_completeness=EvidenceCompleteness.COMPLETE,
        )
    )
    store.append_pipeline_run(
        replace(
            first,
            trace_id="late-trace-one",
            effective_identity="late-identity-one",
            code_version="late-code-one",
            config_version="late-config-one",
            finished_at=late_first_finished,
            failure_summary="late first failure",
            evidence_completeness=EvidenceCompleteness.REDACTED,
        )
    )

    row = store.get_run("project", "run")
    assert (
        row["attempt"],
        row["status"],
        row["trace_id"],
        row["effective_identity"],
        row["code_version"],
        row["config_version"],
    ) == (2, "success", "trace-two", "identity-two", "code-two", "config-two")
    assert row["finished_at"] == second_finished.isoformat()
    assert row["failure_summary"] is None
    assert row["evidence_completeness"] == EvidenceCompleteness.COMPLETE


def test_terminal_stage_status_and_metadata_are_sticky() -> None:
    store = _store_with_run()
    first_finished = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    later_finished = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
    first = RunStage(
        project_id="project",
        run_id="run",
        stage_id="stage",
        stage_type="ingest",
        status="failed",
        finished_at=first_finished,
        metrics={"rows": 10, "bytes": 100},
        error="first failure",
    )
    store.append_stage(first)
    store.append_stage(
        replace(
            first,
            status="success",
            finished_at=later_finished,
            metrics={"rows": 20},
            error="late outcome",
        )
    )
    store.append_stage(replace(first, status="running", finished_at=None, metrics={}, error=None))

    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT status, finished_at, metrics, error FROM run_stage "
            "WHERE project_id = ? AND run_id = ? AND stage_id = ?",
            ("project", "run", "stage"),
        )
        row = cursor.fetchone()
    assert row[0] == "failed"
    assert row[1] == first_finished.isoformat()
    assert row[2] == '{"bytes":100,"rows":10}'
    assert row[3] == "first failure"


def test_previous_event_schema_version_remains_readable() -> None:
    store = _store_with_run()
    assert store.append_event(_event(payload={"value": 1}, event_id="old")) is True
    store.append_event(
        replace(_event(payload={"value": 2}, event_id="previous"), schema_version="0.9")
    )
    assert store.list_events("project", "run")[-1]["schema_version"] == "0.9"


def test_sqlite_migration_is_versioned_and_idempotent() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store._initialize_schema()
    store._initialize_schema()
    with store._transaction() as (_, cursor):
        cursor.execute("SELECT version, checksum FROM run_evidence_schema_version ORDER BY version")
        applied_migrations = cursor.fetchall()
        assert [row[0] for row in applied_migrations] == list(
            range(1, RUN_EVIDENCE_SCHEMA_VERSION + 1)
        )
        assert all(row[1] for row in applied_migrations)
        cursor.execute("SELECT version FROM run_evidence_schema_version")
        assert [row[0] for row in cursor.fetchall()] == list(
            range(1, RUN_EVIDENCE_SCHEMA_VERSION + 1)
        )
        for table in (
            "run_event",
            "run_resource",
            "run_catalog_change",
            "run_quality_result",
            "run_artifact",
        ):
            cursor.execute(f"SELECT attempt FROM {table}")
            assert cursor.description[0][0] == "attempt"


def test_sqlite_migration_fails_closed_for_unknown_or_non_contiguous_versions(tmp_path) -> None:
    database = tmp_path / "unsupported-version.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE run_evidence_schema_version (version INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO run_evidence_schema_version(version) VALUES (99)")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="unsupported run-evidence schema version 99"):
        SQLiteRunEvidenceStore(database)._initialize_schema()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM run_evidence_schema_version")
    connection.executemany(
        "INSERT INTO run_evidence_schema_version(version) VALUES (?)", [(1,), (3,)]
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="non-contiguous migration versions"):
        SQLiteRunEvidenceStore(database)._initialize_schema()


def test_postgres_migration_fails_closed_for_non_contiguous_versions() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("phlo.run_evidence_schema_version",)
    cursor.fetchall.return_value = [(1, None), (3, None)]
    store = PostgresRunEvidenceStore("unused", connection_factory=lambda: connection)

    with pytest.raises(RuntimeError, match="non-contiguous migration versions"):
        store._initialize_schema()

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()
    connection.close.assert_called_once_with()


def test_sqlite_migration_fails_closed_for_checksum_drift(tmp_path) -> None:
    database = tmp_path / "checksum-drift.db"
    store = SQLiteRunEvidenceStore(database)
    store._initialize_schema()
    store._connection.execute(
        "UPDATE run_evidence_schema_version SET checksum = 'unexpected' WHERE version = 1"
    )
    store._connection.commit()

    with pytest.raises(RuntimeError, match="migration checksum drift at version 1"):
        SQLiteRunEvidenceStore(database)._initialize_schema()


def test_core_sink_records_all_correlated_lifecycle_families() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    correlation = HookCorrelation(project_id="project", run_id="run", job_name="job")
    events = [
        IngestionEvent(
            event_type="ingestion.start",
            asset_key="raw",
            table_name="raw",
            group_name="raw",
            correlation=correlation,
        ),
        TransformEvent(
            event_type="transform.start", tool="dbt", asset_key="model", correlation=correlation
        ),
        QualityResultEvent(
            event_type="quality.result",
            asset_key="model",
            check_name="valid",
            passed=True,
            correlation=correlation,
        ),
        PublishEvent(
            event_type="publish.end", asset_key="model", status="success", correlation=correlation
        ),
        LineageEvent(event_type="lineage.edges", edges=[("raw", "model")], correlation=correlation),
    ]

    for event in events:
        provider._handle_event(event)

    assert store.count_events("project", "run") == 5
    assert store.get_run("project", "run")["evidence_completeness"] == "incomplete"


def test_standard_emitter_reconstructs_same_event_identity_for_retry() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    context = IngestionEventContext(
        asset_key="raw",
        table_name="raw",
        group_name="raw",
        project_id="project",
        run_id="run",
    )

    first_bus = type("Bus", (), {"emit": lambda self, event: provider._handle_event(event)})()
    IngestionEventEmitter(context, hook_bus=first_bus).emit_start()
    IngestionEventEmitter(context, hook_bus=first_bus).emit_start()

    assert store.count_events("project", "run") == 1


def test_production_requires_postgres_run_evidence_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_ENVIRONMENT", "production")
    monkeypatch.delenv("PHLO_RUN_EVIDENCE_DB_URL", raising=False)
    monkeypatch.delenv("PHLO_RUN_EVIDENCE_SQLITE_PATH", raising=False)

    with pytest.raises(RuntimeError, match="requires PHLO_RUN_EVIDENCE_DB_URL"):
        default_run_evidence_store()


def test_incomplete_correlation_is_not_persisted() -> None:
    provider = CoreRunEvidenceHookProvider(SQLiteRunEvidenceStore(":memory:"))
    provider._handle_event(
        IngestionEvent(
            event_type="ingestion.start",
            asset_key="raw",
            table_name="raw",
            group_name="raw",
            correlation=HookCorrelation(run_id="run"),
        )
    )
    assert provider._store is not None
    assert provider._store.get_run("unknown", "run") is None


def test_quality_and_lineage_identities_include_logical_operation() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    correlation = HookCorrelation(project_id="project", run_id="run", trace_id="trace-a")
    quality = QualityResultEventContext(
        asset_key="model", project_id="project", run_id="run", correlation=correlation
    )
    quality_bus = type("Bus", (), {"emit": lambda self, event: provider._handle_event(event)})()
    QualityResultEventEmitter(quality, hook_bus=quality_bus).emit_result(
        check_name="nulls", passed=True
    )
    QualityResultEventEmitter(quality, hook_bus=quality_bus).emit_result(
        check_name="uniqueness", passed=True
    )
    retry_quality = replace(correlation, trace_id="trace-b")
    QualityResultEventEmitter(
        replace(quality, correlation=retry_quality), hook_bus=quality_bus
    ).emit_result(check_name="nulls", passed=True)

    lineage = LineageEventContext(project_id="project", run_id="run", correlation=correlation)
    lineage_emitter = LineageEventEmitter(lineage, hook_bus=quality_bus)
    lineage_emitter.emit_edges(edges=[("a", "b")], operation_id="batch-1")
    lineage_emitter.emit_edges(edges=[("a", "b")], operation_id="batch-2")

    assert store.count_events("project", "run") == 4
    assert store.get_run("project", "run")["trace_id"] == "trace-a"


def test_correlated_lineage_requires_explicit_operation_identity() -> None:
    emitter = LineageEventEmitter(
        LineageEventContext(
            project_id="project",
            run_id="run",
            correlation=HookCorrelation(project_id="project", run_id="run"),
        ),
        hook_bus=type("Bus", (), {"emit": lambda self, event: None})(),
    )
    with pytest.raises(ValueError, match="operation_id or event_id"):
        emitter.emit_edges(edges=[("a", "b")])


def test_retry_attempts_get_distinct_stage_evidence() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    for attempt in (1, 2):
        provider._handle_event(
            IngestionEvent(
                event_type="ingestion.end",
                asset_key="raw",
                table_name="raw",
                group_name="raw",
                status="failed" if attempt == 1 else "success",
                correlation=HookCorrelation(project_id="project", run_id="run", attempt=attempt),
            )
        )

    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT attempt FROM run_stage WHERE project_id = ? AND run_id = ? ORDER BY attempt",
            ("project", "run"),
        )
        assert [row[0] for row in cursor.fetchall()] == [1, 2]


def test_retry_attempt_propagates_to_hook_event_stage_and_quality_rows() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    provider._handle_event(
        QualityResultEvent(
            event_type="quality.result",
            asset_key="model",
            check_name="valid",
            passed=True,
            correlation=HookCorrelation(project_id="project", run_id="run", attempt=2),
        )
    )

    with store._transaction() as (_, cursor):
        for table, identifier in (
            ("run_event", "event_id"),
            ("run_stage", "stage_id"),
            ("run_quality_result", "quality_result_id"),
        ):
            cursor.execute(
                f"SELECT attempt FROM {table} WHERE project_id = ? AND run_id = ?",
                ("project", "run"),
            )
            assert [row[0] for row in cursor.fetchall()] == [2], (table, identifier)


def test_normalized_resource_payload_is_redacted() -> None:
    store = _store_with_run()
    store.append_resource(
        RunResource(
            project_id="project",
            run_id="run",
            resource_id="resource",
            uri="postgresql://user:secret@example.test/db",
            staged_objects=["s3://access_token=secret/object"],
        )
    )
    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT uri, staged_objects FROM run_resource WHERE resource_id = ?", ("resource",)
        )
        uri, staged_objects = cursor.fetchone()
    assert "secret" not in uri
    assert "secret" not in staged_objects


def test_observation_persists_provider_resources_and_catalog_change() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            observation_type="iceberg",
            status="success",
            producer="provider",
            resources=[
                {
                    "resource_id": "stage-object-1",
                    "resource_kind": "staged_object",
                    "role": "staged",
                    "resource_identity": {
                        "resource_type": "staged_object",
                        "resource_id": "stage-object-1",
                        "tenant": "project",
                        "attributes": {"classification": "internal"},
                    },
                    "staged_objects": [
                        {"identity": "sha256:abc", "checksum": "abc", "byte_count": 10}
                    ],
                    "metadata": {"watermark": {"status": "unavailable"}},
                }
            ],
            catalog_change={
                "catalog_change_id": "promotion-1",
                "operation": "promotion",
                "catalog_ref": "main",
                "resource_identity": {
                    "resource_type": "catalog",
                    "resource_id": "main",
                    "tenant": "project",
                    "attributes": {"environment": "production"},
                },
                "source_hash": "source",
                "target_hash": "target",
                "merge_outcome": "promoted",
            },
            artifacts=[
                {
                    "artifact_id": "run-log-1",
                    "artifact_kind": "log",
                    "resource_identity": {
                        "resource_type": "log",
                        "resource_id": "run-log-1",
                        "tenant": "project",
                    },
                }
            ],
            correlation=HookCorrelation(project_id="project", run_id="run", attempt=2),
        )
    )

    with store._transaction() as (_, cursor):
        cursor.execute("SELECT attempt, staged_objects, metadata FROM run_resource")
        resource = cursor.fetchone()
        cursor.execute(
            "SELECT attempt, source_hash, target_hash, merge_outcome FROM run_catalog_change"
        )
        change = cursor.fetchone()
    assert resource[0] == 2
    assert "sha256:abc" in resource[1]
    assert resource[2] == '{"watermark":{"status":"unavailable"}}'
    assert change[0] == 2
    assert tuple(change[1:]) == ("source", "target", "promoted")
    report = build_run_report(store, "project", "run", 2)
    assert report.staging[0].resource_identity == ReportResourceIdentity(
        "project",
        "staged_object",
        "stage-object-1",
        "project",
        {"classification": "internal"},
    )
    assert report.catalog_changes[0].resource_identity == ReportResourceIdentity(
        "project", "catalog", "main", "project", {"environment": "production"}
    )
    assert report.artifacts[0].resource_identity == ReportResourceIdentity(
        "project", "log", "run-log-1", "project", {}
    )


@pytest.mark.parametrize(
    "resource_identity",
    [
        None,
        {"resource_type": "dataset", "resource_id": "raw.orders", "tenant": "other"},
        {
            "resource_type": "dataset",
            "resource_id": "raw.orders",
            "tenant": "project",
            "attributes": {"classification": 7},
        },
    ],
)
def test_observation_rejects_resource_without_canonical_producer_identity(
    resource_identity: dict[str, object] | None,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    raw_resource: dict[str, object] = {
        "resource_id": "local-correlation-id",
        "resource_kind": "dataset",
        "role": "output",
    }
    if resource_identity is not None:
        raw_resource["resource_identity"] = resource_identity

    with pytest.raises(ValueError, match="observation resource requires canonical"):
        CoreRunEvidenceHookProvider(store)._handle_event(
            RunEvidenceObservationEvent(
                event_type="run_evidence.observation",
                observation_type="ingest",
                status="success",
                resources=[raw_resource],
                correlation=HookCorrelation(project_id="project", run_id="run"),
            )
        )

    assert store.get_run("project", "run") is None


@pytest.mark.parametrize(
    "resource_identity",
    [
        None,
        {"resource_type": "catalog", "resource_id": "main", "tenant": "other"},
    ],
)
def test_observation_rejects_catalog_change_without_canonical_producer_identity(
    resource_identity: dict[str, object] | None,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    catalog_change: dict[str, object] = {
        "catalog_change_id": "local-correlation-id",
        "operation": "promotion",
        "catalog_ref": "main",
    }
    if resource_identity is not None:
        catalog_change["resource_identity"] = resource_identity

    with pytest.raises(ValueError, match="catalog change requires canonical"):
        CoreRunEvidenceHookProvider(store)._handle_event(
            RunEvidenceObservationEvent(
                event_type="run_evidence.observation",
                observation_type="publish",
                status="success",
                catalog_change=catalog_change,
                correlation=HookCorrelation(project_id="project", run_id="run"),
            )
        )

    assert store.get_run("project", "run") is None


@pytest.mark.parametrize(
    "resource_identity",
    [
        None,
        {"resource_type": "log", "resource_id": "run-log", "tenant": "other"},
    ],
)
def test_observation_rejects_artifact_without_canonical_producer_identity(
    resource_identity: dict[str, object] | None,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    artifact: dict[str, object] = {
        "artifact_id": "run-log",
        "artifact_kind": "log",
    }
    if resource_identity is not None:
        artifact["resource_identity"] = resource_identity

    with pytest.raises(ValueError, match="artifact requires canonical"):
        CoreRunEvidenceHookProvider(store)._handle_event(
            RunEvidenceObservationEvent(
                event_type="run_evidence.observation",
                observation_type="publish",
                status="success",
                artifacts=[artifact],
                correlation=HookCorrelation(project_id="project", run_id="run"),
            )
        )

    assert store.get_run("project", "run") is None


def test_public_observation_boundary_persists_canonical_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    bus = HookBus()
    bus.register_provider(CoreRunEvidenceHookProvider(store), plugin_name="test")
    monkeypatch.setattr("phlo.run_evidence.emit.get_hook_bus", lambda: bus)

    emit_observation(
        project_id="project",
        run_id="run",
        observation_type="publish",
        status="success",
        producer="provider",
        artifacts=[
            {
                "artifact_id": "run-log",
                "artifact_kind": "log",
                "resource_identity": {
                    "resource_type": "log",
                    "resource_id": "run-log",
                    "tenant": "project",
                    "attributes": {"classification": "internal"},
                },
            }
        ],
    )

    report = build_run_report(store, "project", "run", 1)
    assert report.artifacts[0].resource_identity == ReportResourceIdentity(
        "project", "log", "run-log", "project", {"classification": "internal"}
    )


def test_observation_redacts_rows_and_credentials() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            observation_type="ingest",
            status="failed",
            producer="provider",
            error="authorization=TOPSECRET",
            resources=[
                {
                    "resource_kind": "staged_object",
                    "role": "staged",
                    "resource_identity": {
                        "resource_type": "staged_object",
                        "resource_id": "redacted-object",
                        "tenant": "project",
                    },
                    "uri": "https://example.test/object?client_secret=TOPSECRET",
                    "metadata": {"rows": [{"email": "alice@example.test"}]},
                }
            ],
            correlation=HookCorrelation(project_id="project", run_id="run"),
        )
    )
    event_payload = store.list_events("project", "run")[0]["payload"]
    with store._transaction() as (_, cursor):
        cursor.execute("SELECT uri, metadata FROM run_resource")
        uri, metadata = cursor.fetchone()
    for value in (event_payload, uri, metadata):
        assert "TOPSECRET" not in value
        assert "alice@example.test" not in value


def test_event_identity_rejects_cross_run_reuse() -> None:
    store = _store_with_run()
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="other"))
    event = _event(payload={"value": 1})
    assert store.append_event(event) is True
    with pytest.raises(IdempotencyConflict):
        store.append_event(replace(event, run_id="other"))
    with pytest.raises(IdempotencyConflict):
        store.append_event(replace(event, attempt=2))


def test_new_attempt_fields_are_positive_and_staged_objects_are_structured() -> None:
    with pytest.raises(ValueError, match="positive"):
        RunEvent(
            project_id="project",
            run_id="run",
            event_id="e",
            event_type="x",
            producer="p",
            payload={},
            attempt=0,
        )
    with pytest.raises(ValueError, match="stable identity"):
        RunResource(
            project_id="project", run_id="run", resource_id="r", staged_objects=[{"checksum": "x"}]
        )
    with pytest.raises(ValueError, match="positive"):
        RunQualityResult(
            project_id="project", run_id="run", quality_result_id="q", check_id="c", attempt=0
        )
    with pytest.raises(ValueError, match="positive"):
        RunArtifact(
            project_id="project", run_id="run", artifact_id="a", artifact_kind="log", attempt=0
        )


def test_attempt_scoped_child_evidence_and_reports_are_isolated() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run", attempt=2))
    for attempt in (1, 2):
        store.append_resource(
            RunResource(
                project_id="project",
                run_id="run",
                attempt=attempt,
                resource_id=f"resource:{attempt}",
                resource_kind="staged",
            )
        )
        store.append_catalog_change(
            RunCatalogChange(
                project_id="project",
                run_id="run",
                attempt=attempt,
                catalog_change_id=f"catalog:{attempt}",
                operation="promotion",
                source_hash=f"source:{attempt}",
                target_hash=f"target:{attempt}",
            )
        )
        store.append_quality_result(
            RunQualityResult(
                project_id="project",
                run_id="run",
                attempt=attempt,
                quality_result_id=f"quality:{attempt}",
                check_id="gate",
                passed=attempt == 2,
            )
        )
        store.append_artifact(
            RunArtifact(
                project_id="project",
                run_id="run",
                attempt=attempt,
                artifact_id=f"artifact:{attempt}",
                artifact_kind="quality-report",
            )
        )

    assert [row["attempt"] for row in store.list_resources("project", "run", attempt=1)] == [1]
    assert [row["attempt"] for row in store.list_resources("project", "run", attempt=2)] == [2]
    assert [
        row["source_hash"] for row in store.list_catalog_changes("project", "run", attempt=2)
    ] == ["source:2"]
    assert [row["passed"] for row in store.list_quality_results("project", "run", attempt=1)] == [0]
    assert [row["attempt"] for row in store.list_artifacts("project", "run", attempt=2)] == [2]
    assert len(store.list_resources("project", "run")) == 2

    with store._transaction() as (_, cursor):
        for table in ("run_lineage_edge", "run_quality_result", "run_artifact"):
            cursor.execute(f"PRAGMA table_info({table})")
            assert "attempt" in {row[1] for row in cursor.fetchall()}


def test_sqlite_v1_store_upgrades_additive_instrumentation_columns(tmp_path) -> None:
    database = tmp_path / "run-evidence.db"
    connection = sqlite3.connect(database)
    foundation = Path(__file__).parents[2] / "src/phlo/sql/002_create_run_evidence_sqlite.sql"
    foundation_sql = foundation.read_text(encoding="utf-8")
    connection.executescript(foundation_sql)
    connection.commit()
    connection.close()

    store = SQLiteRunEvidenceStore(str(database))
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run"))
    store.append_resource(
        RunResource(
            project_id="project",
            run_id="run",
            resource_id="resource",
            attempt=2,
            schema_hash_before="before",
            schema_hash_after="after",
        )
    )

    with store._transaction() as (_, cursor):
        cursor.execute("PRAGMA table_info(run_event)")
        event_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(run_resource)")
        resource_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(run_lineage_edge)")
        lineage_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(run_quality_result)")
        quality_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(run_artifact)")
        artifact_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("SELECT version FROM run_evidence_schema_version ORDER BY version")
        versions = [row[0] for row in cursor.fetchall()]
    assert "attempt" in event_columns
    assert {"attempt", "schema_hash_before", "schema_hash_after", "metadata"} <= resource_columns
    assert "attempt" in lineage_columns
    assert "attempt" in quality_columns
    assert "attempt" in artifact_columns
    assert versions == list(range(1, RUN_EVIDENCE_SCHEMA_VERSION + 1))


def test_v2_field_order_and_additive_checksums_are_compatibility_safe() -> None:
    from dataclasses import fields

    assert [field.name for field in fields(RunResource)][-5:] == [
        "attempt",
        "schema_hash_before",
        "schema_hash_after",
        "metadata",
        "resource_ref",
    ]
    assert [field.name for field in fields(RunCatalogChange)][-3:] == [
        "attempt",
        "quality_decision_id",
        "resource_ref",
    ]
    assert [field.name for field in fields(RunArtifact)][-2:] == ["attempt", "resource_ref"]
    assert [field.name for field in fields(RunLineageEdge)][-3:] == [
        "attempt",
        "source_resource_ref",
        "target_resource_ref",
    ]

    store = _store_with_run()
    resource = RunResource(project_id="project", run_id="run", resource_id="resource")
    catalog_change = RunCatalogChange(
        project_id="project", run_id="run", catalog_change_id="catalog", operation="promotion"
    )
    lineage = RunLineageEdge(
        project_id="project", run_id="run", lineage_edge_id="lineage", source="a", target="b"
    )
    store.append_resource(resource)
    store.append_catalog_change(catalog_change)
    store.append_lineage_edge(lineage)

    with store._transaction() as (_, cursor):
        cursor.execute(
            "SELECT record_checksum FROM run_resource WHERE resource_id = ?", ("resource",)
        )
        resource_checksum = cursor.fetchone()[0]
        cursor.execute(
            "SELECT record_checksum FROM run_catalog_change WHERE catalog_change_id = ?",
            ("catalog",),
        )
        catalog_checksum = cursor.fetchone()[0]
        cursor.execute(
            "SELECT record_checksum FROM run_lineage_edge WHERE lineage_edge_id = ?",
            ("lineage",),
        )
        lineage_checksum = cursor.fetchone()[0]
    assert resource_checksum == payload_checksum(_resource_checksum_payload(resource))
    assert catalog_checksum == payload_checksum(_catalog_checksum_payload(catalog_change))
    assert lineage_checksum == payload_checksum(_lineage_checksum_payload(lineage))
    with pytest.raises(IdempotencyConflict):
        store.append_resource(replace(resource, schema_hash_before="changed"))
    with pytest.raises(IdempotencyConflict):
        store.append_resource(replace(resource, schema_hash_after="changed"))
    with pytest.raises(IdempotencyConflict):
        store.append_resource(replace(resource, metadata={"source": "changed"}))
    with pytest.raises(IdempotencyConflict):
        store.append_catalog_change(replace(catalog_change, quality_decision_id="quality-real"))
    with pytest.raises(IdempotencyConflict):
        store.append_lineage_edge(replace(lineage, attempt=2))


def test_true_v2_to_v4_upgrade_is_idempotent_and_marks_legacy_identity_incomplete(tmp_path) -> None:
    store = _make_sqlite_v2_store(tmp_path / "v2-evidence.db")
    fixture = _insert_v2_fixture_rows(store)
    before_checksums = _fixture_checksums(store)
    with store._transaction() as (_, cursor):
        cursor.execute("SELECT version FROM run_evidence_schema_version ORDER BY version")
        assert [row[0] for row in cursor.fetchall()] == [1, 2]

    store._initialized = False
    store._initialize_schema()
    store._initialized = False
    store._initialize_schema()

    with store._transaction() as (_, cursor):
        cursor.execute("SELECT version FROM run_evidence_schema_version ORDER BY version")
        assert [row[0] for row in cursor.fetchall()] == list(
            range(1, RUN_EVIDENCE_SCHEMA_VERSION + 1)
        )
    assert _fixture_checksums(store) == before_checksums

    assert store.append_event(fixture["event"]) is False
    assert store.append_resource(fixture["resource"]) is None
    assert store.append_catalog_change(fixture["catalog"]) is None
    assert store.append_quality_result(fixture["quality"]) is None
    assert store.append_artifact(fixture["artifact"]) is None
    report = build_run_report(store, fixture["project_id"], fixture["run_id"], fixture["attempt"])
    assert report.staging[0].resource_identity is None
    assert report.staging[0].resource_identity_status == "incomplete"
    assert report.artifacts[0].resource_identity is None
    assert any(
        gap.field == "resource_identities" and gap.status == "incomplete" for gap in report.gaps
    )
    with pytest.raises(IdempotencyConflict):
        store.append_resource(replace(fixture["resource"], uri="https://changed.example"))
    with pytest.raises(IdempotencyConflict):
        store.append_catalog_change(replace(fixture["catalog"], quality_decision_id="forged"))

    lineage_v1 = RunLineageEdge(
        project_id=fixture["project_id"],
        run_id=fixture["run_id"],
        lineage_edge_id="legacy-lineage-v1",
        source="raw.events",
        target="analytics.events",
        attempt=1,
    )
    lineage_v2 = replace(lineage_v1, lineage_edge_id="legacy-lineage-v2", attempt=2)
    store.append_lineage_edge(lineage_v1)
    store.append_lineage_edge(lineage_v2)
    assert len(store.list_lineage_edges(fixture["project_id"], fixture["run_id"], attempt=1)) == 1
    assert len(store.list_lineage_edges(fixture["project_id"], fixture["run_id"], attempt=2)) == 1
    _assert_public_record_shapes(store, fixture)
    projection = _timestamp_projection(store, fixture)
    assert projection["run"] == {
        "started_at": "2026-07-01T00:00:00+00:00",
        "finished_at": "2026-07-01T00:00:00+00:00",
    }
    assert projection["event"] == {"observed_at": "2026-07-01T00:00:00+00:00"}
    assert projection["stage"] == {
        "started_at": "2026-07-01T01:02:03+00:00",
        "finished_at": "2026-07-01T01:03:04+00:00",
    }
    _assert_nullable_public_timestamps(store, fixture)


def test_true_v2_to_v3_upgrade_is_idempotent_and_compatible_postgres() -> None:
    dsn = os.environ.get("PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN for the live PostgreSQL upgrade gate")
    pytest.importorskip("psycopg2")

    schema = f"run_evidence_v2_{uuid4().hex}"
    sql_root = Path(__file__).parents[2] / "src" / "phlo" / "sql"

    class TemporaryPostgresStore(PostgresRunEvidenceStore):
        table_prefix = f'"{schema}".'

        def apply_migrations(self, names: tuple[str, ...]) -> None:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    for name in names:
                        sql = (sql_root / name).read_text(encoding="utf-8")
                        sql = sql.replace(
                            "CREATE SCHEMA IF NOT EXISTS phlo;",
                            f'CREATE SCHEMA IF NOT EXISTS "{schema}";',
                        ).replace("phlo.", f'"{schema}".')
                        cursor.execute(sql)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        def _initialize_schema(self) -> None:
            self.apply_migrations(
                (
                    "002_create_run_evidence.sql",
                    "003_reconcile_run_evidence.sql",
                    "004_run_evidence_instrumentation.sql",
                    "005_run_evidence_resource_identity.sql",
                )
            )

    store = TemporaryPostgresStore(dsn)
    try:
        store.apply_migrations(("002_create_run_evidence.sql", "003_reconcile_run_evidence.sql"))
        store._initialized = True
        fixture = _insert_v2_fixture_rows(store)
        with store._transaction() as (_, cursor):
            cursor.execute(
                'SELECT version FROM "' + schema + '".run_evidence_schema_version ORDER BY version'
            )
            assert [row[0] for row in cursor.fetchall()] == [1, 2]
        before_checksums = _fixture_checksums(store)

        store._initialized = False
        store._initialize_schema()
        store._initialized = False
        store._initialize_schema()
        with store._transaction() as (_, cursor):
            cursor.execute(
                'SELECT version FROM "' + schema + '".run_evidence_schema_version ORDER BY version'
            )
            assert [row[0] for row in cursor.fetchall()] == [1, 2, 3, 4]
        assert _fixture_checksums(store) == before_checksums
        assert store.list_events(fixture["project_id"], fixture["run_id"])[0]["payload"] == {
            "legacy": True
        }
        assert store.append_event(fixture["event"]) is False
        assert store.append_resource(fixture["resource"]) is None
        assert store.append_catalog_change(fixture["catalog"]) is None
        assert store.append_quality_result(fixture["quality"]) is None
        assert store.append_artifact(fixture["artifact"]) is None
        with pytest.raises(IdempotencyConflict):
            store.append_resource(replace(fixture["resource"], uri="https://changed.example"))
        with pytest.raises(IdempotencyConflict):
            store.append_catalog_change(replace(fixture["catalog"], quality_decision_id="forged"))
        baseline = _make_sqlite_v2_store(Path(":memory:"))
        baseline_fixture = _insert_v2_fixture_rows(baseline)
        assert _timestamp_projection(store, fixture) == _timestamp_projection(
            baseline, baseline_fixture
        )
        _assert_public_record_shapes(store, fixture)
        _assert_nullable_public_timestamps(store, fixture)
    finally:
        connection = store._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            connection.commit()
        finally:
            connection.close()


def test_wap_rejection_is_failed_terminal_publish_evidence() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    timestamp = datetime.now(UTC)
    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id="wap-rejected",
            observation_type="publish",
            status="rejected",
            producer="phlo-dagster-nessie",
            timestamp=timestamp,
            catalog_change={
                "operation": "promotion",
                "catalog_ref": "main",
                "merge_outcome": "rejected_quality",
                "resource_identity": {
                    "resource_type": "catalog",
                    "resource_id": "main",
                    "tenant": "project",
                },
            },
            correlation=HookCorrelation(project_id="project", run_id="run", attempt=1),
        )
    )
    run = store.get_run("project", "run")
    assert run is not None
    assert run["status"] == "running"
    assert run["finished_at"] is None
    assert (
        store.list_catalog_changes("project", "run", attempt=1)[0]["merge_outcome"]
        == "rejected_quality"
    )
    assert store.list_events("project", "run")[0]["event_type"] == "run_evidence.observation"


def test_terminal_only_observation_does_not_fabricate_stage_start() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    timestamp = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    CoreRunEvidenceHookProvider(store)._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id="terminal-only",
            observation_type="ingestion",
            status="success",
            run_status="success",
            producer="test",
            timestamp=timestamp,
            correlation=HookCorrelation(project_id="project", run_id="run"),
        )
    )

    with store._transaction() as (_, cursor):
        cursor.execute("SELECT started_at, finished_at FROM run_stage")
        started_at, finished_at = cursor.fetchone()
    assert started_at is None
    assert finished_at == timestamp.isoformat()


def test_terminal_observation_preserves_prior_stage_start() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    started = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    finished = datetime(2026, 7, 14, 12, 1, tzinfo=UTC)
    correlation = HookCorrelation(project_id="project", run_id="run")
    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id="stage-start",
            observation_type="ingestion",
            status="running",
            producer="test",
            timestamp=started,
            correlation=correlation,
        )
    )
    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id="stage-finish",
            observation_type="ingestion",
            status="success",
            run_status="success",
            producer="test",
            timestamp=finished,
            correlation=correlation,
        )
    )

    with store._transaction() as (_, cursor):
        cursor.execute("SELECT started_at, finished_at FROM run_stage")
        started_at, finished_at = cursor.fetchone()
    assert started_at == started.isoformat()
    assert finished_at == finished.isoformat()


def test_wap_rejection_preserves_authoritative_success_status() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(
            project_id="project", run_id="run", status="success", finished_at=datetime.now(UTC)
        )
    )
    provider = CoreRunEvidenceHookProvider(store)
    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id="wap-rejected-existing",
            observation_type="publish",
            status="rejected",
            run_status="success",
            producer="phlo-dagster-nessie",
            catalog_change={
                "operation": "promotion",
                "merge_outcome": "rejected_quality",
                "resource_identity": {
                    "resource_type": "catalog",
                    "resource_id": "main",
                    "tenant": "project",
                },
            },
            correlation=HookCorrelation(project_id="project", run_id="run"),
        )
    )
    run = store.get_run("project", "run")
    assert run is not None and run["status"] == "success"


def test_terminal_success_observation_uses_event_timestamp_without_fabricating_start() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    provider = CoreRunEvidenceHookProvider(store)
    timestamp = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    provider._handle_event(
        RunEvidenceObservationEvent(
            event_type="run_evidence.observation",
            event_id="successful-observation",
            observation_type="ingest",
            status="success",
            run_status="success",
            producer="phlo-dlt",
            timestamp=timestamp,
            correlation=HookCorrelation(project_id="project", run_id="run", attempt=2),
        )
    )

    run = store.get_run("project", "run")
    assert run is not None
    assert run["status"] == "success"
    assert run["finished_at"] == timestamp.isoformat()
    assert run["started_at"] is None


@pytest.mark.parametrize("sink_error", [RuntimeError("store unavailable"), TypeError("bad row")])
def test_post_submit_observation_sink_failure_does_not_escape(
    monkeypatch: pytest.MonkeyPatch, sink_error: Exception
) -> None:
    class FailingBus:
        def emit(self, _event: object) -> None:
            raise sink_error

    logger = MagicMock()
    monkeypatch.setattr("phlo.run_evidence.emit.get_hook_bus", lambda: FailingBus())
    monkeypatch.setattr("phlo.run_evidence.emit.logger", logger)

    emit_observation(
        project_id="project",
        run_id="run",
        attempt=2,
        observation_type="iceberg",
        status="success",
        producer="provider",
    )

    logger.error.assert_called_once()
    fields = logger.error.call_args.kwargs
    assert fields["project_id"] == "project"
    assert fields["run_id"] == "run"
    assert fields["attempt"] == "2"
    assert fields["error_type"] == type(sink_error).__name__


def test_observation_boundary_logs_invalid_correlation_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("phlo.run_evidence.emit.logger", logger)

    emit_observation(
        project_id="project",
        run_id="run",
        attempt=0,
        observation_type="ingest",
        status="success",
        producer="provider",
    )

    class Unstringifiable:
        def __str__(self) -> str:
            raise RuntimeError("must not escape")

    emit_observation(
        project_id="project",
        run_id="run",
        attempt=1,
        observation_type="ingest",
        status="success",
        producer="provider",
        identity_parts=(Unstringifiable(),),
    )

    assert logger.error.call_count == 2
    for call in logger.error.call_args_list:
        assert call.kwargs["project_id"] == "project"
        assert call.kwargs["run_id"] == "run"


def test_list_runs_returns_durable_runs_newest_first(tmp_path: Path) -> None:
    store = SQLiteRunEvidenceStore(tmp_path / "runs.sqlite")
    store.append_pipeline_run(
        PipelineRun(
            project_id="finance",
            run_id="daily-orders",
            attempt=2,
            status="success",
            started_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 12, 5, tzinfo=UTC),
        )
    )
    store.append_pipeline_run(
        PipelineRun(
            project_id="finance",
            run_id="hourly-orders",
            attempt=1,
            status="running",
            started_at=datetime(2026, 8, 19, 9, 0, tzinfo=UTC),
        )
    )

    rows = store.list_runs()

    assert [row["run_id"] for row in rows] == ["hourly-orders", "daily-orders"]
    finance = next(row for row in rows if row["run_id"] == "daily-orders")
    assert finance["project_id"] == "finance"
    assert finance["attempt"] == 2


def test_list_runs_empty_when_no_evidence(tmp_path: Path) -> None:
    assert SQLiteRunEvidenceStore(tmp_path / "empty.sqlite").list_runs() == []


def test_list_runs_page_is_stable_across_equal_activity_inserts_and_deletes(tmp_path: Path) -> None:
    store = SQLiteRunEvidenceStore(tmp_path / "runs.sqlite")
    activity = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    for run_id in ("a", "b", "c"):
        store.append_pipeline_run(
            PipelineRun(project_id="project", run_id=run_id, started_at=activity)
        )

    first_page, cursor = store.list_runs_page(limit=2)
    assert [row["run_id"] for row in first_page] == ["a", "b"]
    assert cursor is not None

    store.append_pipeline_run(PipelineRun(project_id="project", run_id="aa", started_at=activity))
    with store._transaction() as (_, sql_cursor):
        sql_cursor.execute(
            "DELETE FROM pipeline_run WHERE project_id = ? AND run_id = ?", ("project", "a")
        )

    second_page, next_cursor = store.list_runs_page(limit=2, cursor=cursor)
    assert [row["run_id"] for row in second_page] == ["c"]
    assert next_cursor is None

    with store._read_transaction() as (_, sql_cursor):
        sql_cursor.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM pipeline_run "
            "ORDER BY COALESCE(finished_at, started_at, created_at) DESC, project_id, run_id LIMIT 2"
        )
        assert "idx_pipeline_run_activity_keyset" in " ".join(
            str(cell) for row in sql_cursor for cell in row
        )
