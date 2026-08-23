"""Tests for pipeline run evidence reconciliation against the SQLite evidence store.

Reconciliation must be idempotent and attempt-scoped, never invent lifecycle facts from
absent or contradictory evidence, and preserve immutable decision history.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from phlo.run_evidence import (
    EvidenceCompleteness,
    IdempotencyConflict,
    PipelineRun,
    RequiredEvidenceProfile,
    RequiredEvidenceRecord,
    RequiredEvidenceStage,
    RunArtifact,
    RunCatalogChange,
    RunEvent,
    RunEvidenceNotFound,
    RunEvidenceUnavailable,
    RunLookupOutcome,
    RunObservation,
    RunQualityResult,
    RunReconciler,
    RunResource,
    RunStage,
    SQLiteRunEvidenceStore,
)

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _event(
    event_type: str,
    *,
    attempt: int = 1,
    event_id: str | None = None,
    payload: dict | None = None,
) -> RunEvent:
    return RunEvent(
        project_id="project",
        run_id="run",
        event_id=event_id or event_type,
        event_type=event_type,
        producer="source",
        payload=payload or {},
        observed_at=NOW,
        attempt=attempt,
    )


def _profile(*stages: RequiredEvidenceStage) -> RequiredEvidenceProfile:
    return RequiredEvidenceProfile(profile_id="pipeline-v1", version="1", stages=stages)


def _observation(
    *events: RunEvent, status: str | None = "success", attempt: int = 1, **kwargs
) -> RunObservation:
    started_at = kwargs.pop("started_at", NOW - timedelta(minutes=1))
    finished_at = kwargs.pop(
        "finished_at",
        NOW if status in {"success", "failed", "cancelled", "canceled", "no_data"} else None,
    )
    return RunObservation(
        project_id="project",
        run_id="run",
        attempt=attempt,
        pipeline_name="pipeline",
        provider="fake",
        provider_run_id="provider-run",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        source="fake",
        events=tuple(events),
        **kwargs,
    )


class _Source:
    name = "fake"

    def __init__(self, observation: RunObservation | None):
        self.observation = observation

    def observe_run(self, project_id: str, run_id: str) -> RunObservation | None:
        return self.observation


def test_success_requires_durable_run_terminal_and_required_stage() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal")))
    profile = _profile(
        RequiredEvidenceStage(
            "transform", required_event_types=("stage.end",), required_status="success"
        )
    )

    decision = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    assert decision.status == "success"
    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "stage:transform" in decision.missing_evidence
    assert store.get_run("project", "run")["status"] == "success"


def test_stage_success_or_table_readiness_cannot_invent_pipeline_success() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            _event("stage.end"),
            status="success",
            stages=(
                RunStage(
                    project_id="project",
                    run_id="run",
                    stage_id="transform-1",
                    stage_type="transform",
                    status="success",
                ),
            ),
        )
    )

    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "event:run.terminal" in decision.missing_evidence


def test_failed_and_cancelled_profiles_can_be_complete_without_claiming_success() -> None:
    for status, stage_status in (("failed", "failed"), ("canceled", "cancelled")):
        store = SQLiteRunEvidenceStore(":memory:")
        terminal = _event("run.terminal", event_id=f"terminal-{status}", payload={"status": status})
        stage_event = _event(
            "stage.end", event_id=f"stage-{status}", payload={"status": stage_status}
        )
        source = _Source(
            _observation(
                terminal,
                stage_event,
                status=status,
                stages=(
                    RunStage(
                        project_id="project",
                        run_id="run",
                        stage_id=f"stage-{status}",
                        stage_type="transform",
                        status=stage_status,
                    ),
                ),
            )
        )
        profile = _profile(
            RequiredEvidenceStage(
                "transform",
                required_event_types=("stage.end",),
                allowed_statuses=("success", "failed", "cancelled"),
            )
        )

        decision = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

        assert decision.status == ("cancelled" if status == "canceled" else status)
        assert decision.evidence_completeness is EvidenceCompleteness.COMPLETE


def test_explicit_no_data_is_terminal_but_absence_is_not_no_data() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.no_data"), status="no_data"))
    decision = RunReconciler(store, source).reconcile(
        "project", "run", _profile(RequiredEvidenceStage("transform", allow_no_data=True)), now=NOW
    )
    assert decision.status == "no_data"
    assert decision.evidence_completeness is EvidenceCompleteness.COMPLETE


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_contradictory_success_and_no_data_events_do_not_waive_terminal_failure(
    status: str,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            _event("run.terminal", payload={"status": "success"}),
            _event("run.no_data", event_id=f"no-data-{status}"),
            status=status,
        )
    )
    profile = _profile(RequiredEvidenceStage("transform", allow_no_data=True))

    decision = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    assert decision.status == status
    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "stage:transform" in decision.missing_evidence
    assert "event:contradictory_run_terminal_status" in decision.missing_evidence


def test_hard_loss_uses_explicit_heartbeat_threshold_without_guessing_failure() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            status="running",
            started_at=NOW - timedelta(minutes=20),
            heartbeat_at=NOW - timedelta(minutes=10),
        )
    )
    decision = RunReconciler(store, source, stale_after=timedelta(minutes=5)).reconcile(
        "project", "run", _profile(), now=NOW
    )

    assert decision.status == "abandoned"
    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert store.get_run("project", "run")["failure_summary"] is None


def test_abandoned_scan_is_idempotent_and_does_not_invent_finish_time() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            status="running",
            started_at=NOW - timedelta(minutes=20),
            heartbeat_at=NOW - timedelta(minutes=10),
        )
    )
    reconciler = RunReconciler(store, source, stale_after=timedelta(minutes=5))

    first = reconciler.reconcile("project", "run", _profile(), now=NOW)
    second = reconciler.reconcile("project", "run", _profile(), now=NOW + timedelta(minutes=1))

    assert first.status == second.status == "abandoned"
    assert first.decision_id == second.decision_id
    assert store.get_run("project", "run")["finished_at"] is None


def test_heartbeat_changes_create_a_new_auditable_decision() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    first = _observation(status="running", heartbeat_at=NOW - timedelta(minutes=1))
    source = _Source(first)
    reconciler = RunReconciler(store, source, stale_after=timedelta(minutes=5))
    reconciler.reconcile("project", "run", _profile(), now=NOW)
    source.observation = replace(first, heartbeat_at=NOW)
    reconciler.reconcile("project", "run", _profile(), now=NOW)

    decisions = store.list_reconciliation_decisions("project", "run")
    assert len(decisions) == 2
    assert decisions[0]["decision_id"] != decisions[1]["decision_id"]


def test_duplicate_reconciliation_is_idempotent() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal")))
    reconciler = RunReconciler(store, source)
    first = reconciler.reconcile("project", "run", _profile(), now=NOW)
    second = reconciler.reconcile("project", "run", _profile(), now=NOW + timedelta(seconds=1))

    assert first.decision_id == second.decision_id
    assert len(store.list_reconciliation_decisions("project", "run")) == 1


def test_duplicate_reconciliation_returns_stored_decision_without_aggregate_mutation() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal")))
    reconciler = RunReconciler(store, source)

    first = reconciler.reconcile("project", "run", _profile(), now=NOW)
    run_before = store.get_run("project", "run")
    rows_before = store.list_reconciliation_decisions("project", "run")
    second = reconciler.reconcile("project", "run", _profile(), now=NOW + timedelta(minutes=1))

    assert second == first
    assert store.get_run("project", "run") == run_before
    assert store.list_reconciliation_decisions("project", "run") == rows_before


def test_late_required_record_identity_creates_a_follow_up_decision() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal")))
    profile = RequiredEvidenceProfile(
        profile_id="pipeline-v1",
        version="1",
        required_records=(RequiredEvidenceRecord("resource"),),
    )

    first = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)
    store.append_resource(
        RunResource(
            project_id="project",
            run_id="run",
            resource_id="resource-1",
            normalized_identity="table://orders",
        )
    )
    second = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    assert first.decision_id != second.decision_id
    assert len(store.list_reconciliation_decisions("project", "run")) == 2


def test_evidence_state_precedence_is_global_and_observation_state_cannot_downgrade_it() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run"))
    for artifact_id, status in (
        ("redacted", EvidenceCompleteness.REDACTED),
        ("missing", EvidenceCompleteness.MISSING),
    ):
        store.append_artifact(
            RunArtifact(
                project_id="project",
                run_id="run",
                artifact_id=artifact_id,
                artifact_kind="log",
                status=status,
            )
        )
    source = _Source(
        _observation(
            _event("run.terminal"),
            evidence_state=EvidenceCompleteness.MISSING,
        )
    )
    profile = RequiredEvidenceProfile(
        profile_id="pipeline-v1",
        version="1",
        required_records=(
            RequiredEvidenceRecord("artifact"),
            RequiredEvidenceRecord("artifact"),
        ),
    )

    decision = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.REDACTED


def test_late_event_creates_follow_up_decision_and_preserves_immutable_history() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    first = _observation(_event("run.terminal"))
    source = _Source(first)
    profile = _profile(RequiredEvidenceStage("transform", required_event_types=("stage.end",)))
    reconciler = RunReconciler(store, source)
    first_decision = reconciler.reconcile("project", "run", profile, now=NOW)
    source.observation = replace(
        first,
        events=first.events + (_event("stage.end", event_id="late-stage"),),
        stages=(
            RunStage(
                project_id="project",
                run_id="run",
                stage_id="transform-1",
                stage_type="transform",
                status="success",
            ),
        ),
    )
    second_decision = reconciler.reconcile(
        "project", "run", profile, now=NOW + timedelta(minutes=1)
    )

    assert first_decision.decision_id != second_decision.decision_id
    assert second_decision.evidence_completeness is EvidenceCompleteness.COMPLETE
    assert len(store.list_events("project", "run")) == 2
    assert len(store.list_reconciliation_decisions("project", "run")) == 2


def test_mutable_stage_state_changes_create_a_new_decision() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    stage = RunStage(
        project_id="project",
        run_id="run",
        stage_id="transform-1",
        stage_type="transform",
        status="running",
    )
    first_observation = _observation(_event("run.terminal"), stages=(stage,))
    source = _Source(first_observation)
    profile = _profile(RequiredEvidenceStage("transform", required_status="success"))
    first = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    source.observation = replace(first_observation, stages=(replace(stage, status="success"),))
    second = RunReconciler(store, source).reconcile(
        "project", "run", profile, now=NOW + timedelta(minutes=1)
    )

    assert first.decision_id != second.decision_id
    assert second.evidence_completeness is EvidenceCompleteness.COMPLETE


def test_reconciliation_is_project_and_attempt_scoped() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    one = replace(_observation(replace(_event("run.terminal"), project_id="one")), project_id="one")
    two = replace(_observation(replace(_event("run.terminal"), project_id="two")), project_id="two")
    source_one = _Source(one)
    source_two = _Source(two)
    profile = _profile()
    RunReconciler(store, source_one).reconcile("one", "run", profile, now=NOW)
    RunReconciler(store, source_two).reconcile("two", "run", profile, now=NOW)

    assert store.get_run("one", "run")["status"] == "success"
    assert store.get_run("two", "run")["status"] == "success"
    assert len(store.list_reconciliation_decisions("one", "run")) == 1
    assert len(store.list_reconciliation_decisions("two", "run")) == 1


def test_duplicate_event_does_not_mutate_run_from_replay_run_object() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    first = PipelineRun(project_id="project", run_id="run", attempt=1, status="running")
    store.append_event(_event("run.start"), run=first)
    store.append_event(
        _event("run.start"),
        run=replace(first, status="success"),
    )

    row = store.get_run("project", "run")
    assert row["attempt"] == 1
    assert row["status"] == "running"


def test_provider_lookup_unavailable_does_not_create_or_change_a_run() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run", attempt=2))
    with pytest.raises(RunEvidenceUnavailable):
        RunReconciler(store, _Source(None)).reconcile("project", "run", _profile(), now=NOW)
    assert store.get_run("project", "run")["attempt"] == 2
    assert store.list_reconciliation_decisions("project", "run") == []


def test_authoritative_absence_is_typed_and_does_not_create_a_phantom_run() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        RunObservation(
            project_id="project",
            run_id="missing",
            source="fake",
            evidence_state=EvidenceCompleteness.MISSING,
            lookup_outcome=RunLookupOutcome.ABSENT,
        )
    )

    with pytest.raises(RunEvidenceNotFound):
        RunReconciler(store, source).reconcile("project", "missing", _profile(), now=NOW)
    assert store.get_run("project", "missing") is None
    assert store.list_reconciliation_decisions("project", "missing") == []


def test_authoritative_absence_marks_existing_orphan_without_inventing_lifecycle_fields() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(
            project_id="project",
            run_id="orphan",
            attempt=2,
            status="running",
            started_at=NOW - timedelta(minutes=3),
        )
    )
    source = _Source(
        RunObservation(
            project_id="project",
            run_id="orphan",
            source="fake",
            evidence_state=EvidenceCompleteness.MISSING,
            lookup_outcome=RunLookupOutcome.ABSENT,
        )
    )

    decision = RunReconciler(store, source).reconcile("project", "orphan", _profile(), now=NOW)

    row = store.get_run("project", "orphan")
    assert decision.evidence_completeness is EvidenceCompleteness.MISSING
    assert decision.status == "running"
    assert row["status"] == "running"
    assert row["attempt"] == 2
    assert row["started_at"].startswith((NOW - timedelta(minutes=3)).isoformat())
    assert row["finished_at"] is None


def test_profile_identity_and_status_validation_is_strict() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        RequiredEvidenceStage("transform", required_status="success", allowed_statuses=("failed",))
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal")))
    with pytest.raises(ValueError, match="does not match"):
        RunReconciler(store, source).reconcile(
            "project",
            "run",
            RequiredEvidenceProfile("profile", "1", pipeline_name="other"),
            now=NOW,
        )

    with pytest.raises(ValueError, match="resource records"):
        RequiredEvidenceRecord("resource", required_status="ready")
    with pytest.raises(ValueError, match="quality_result"):
        RequiredEvidenceRecord("quality_result", required_status="complete")
    with pytest.raises(ValueError, match="catalog_change"):
        RequiredEvidenceRecord("catalog_change", required_status="complete")


def test_unknown_provider_status_is_unsupported_not_abandoned() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            status="provider_status_typo",
            started_at=NOW - timedelta(minutes=20),
            heartbeat_at=NOW - timedelta(minutes=10),
        )
    )

    decision = RunReconciler(store, source, stale_after=timedelta(minutes=5)).reconcile(
        "project", "run", _profile(), now=NOW
    )

    assert decision.status == "unsupported"
    assert decision.reason == "unsupported_provider_status"


def test_contradictory_terminal_payload_cannot_complete_success() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal", payload={"status": "failed"})))

    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert decision.status == "success"
    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "contradictory_run_terminal_status" in decision.reason


def test_terminal_profile_requires_finished_at_and_heartbeat_cannot_be_future() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    no_finished = _observation(_event("run.terminal"), finished_at=None)
    source = _Source(no_finished)
    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)
    assert "run:finished_at" in decision.missing_evidence
    future = _observation(
        status="running",
        started_at=NOW - timedelta(minutes=20),
        heartbeat_at=NOW + timedelta(minutes=2),
    )
    source.observation = future
    decision = RunReconciler(store, source, stale_after=timedelta(minutes=5)).reconcile(
        "project", "run", _profile(), now=NOW
    )
    assert decision.status == "running"
    assert decision.reason == "invalid_heartbeat"


def test_missing_started_at_is_not_fabricated() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            _event("run.terminal"),
            started_at=None,
            finished_at=NOW,
        )
    )

    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "run:started_at" in decision.missing_evidence
    assert store.get_run("project", "run")["started_at"] is None


def test_materially_future_run_timestamps_are_incomplete_and_auditable() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(
            _event("run.terminal"),
            started_at=NOW + timedelta(minutes=2),
            finished_at=NOW + timedelta(minutes=3),
        )
    )

    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "run:started_at_in_future" in decision.missing_evidence
    assert "run:finished_at_in_future" in decision.missing_evidence
    assert decision.reason.startswith("missing_required_evidence")


@pytest.mark.parametrize(
    "artifact_status",
    ["missing", "expired", "redacted"],
)
def test_timing_defects_preserve_stronger_artifact_availability_state(artifact_status: str) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run"))
    store.append_artifact(
        RunArtifact(
            project_id="project",
            run_id="run",
            artifact_id=f"artifact-{artifact_status}",
            artifact_kind="log",
            status=EvidenceCompleteness(artifact_status),
        )
    )
    source = _Source(
        _observation(
            _event("run.terminal"),
            started_at=NOW + timedelta(minutes=2),
            finished_at=NOW + timedelta(minutes=2),
        )
    )
    decision = RunReconciler(store, source).reconcile(
        "project",
        "run",
        RequiredEvidenceProfile(
            profile_id="pipeline-v1",
            version="1",
            required_records=(RequiredEvidenceRecord("artifact"),),
        ),
        now=NOW,
    )

    assert decision.evidence_completeness is EvidenceCompleteness(artifact_status)
    assert "run:started_at_in_future" in decision.missing_evidence


def test_event_and_stage_timestamp_violations_are_incomplete_and_change_identity() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    terminal = _event("run.terminal", payload={"status": "success"})
    future_event = replace(terminal, event_id="future", observed_at=NOW + timedelta(minutes=2))
    invalid_stage = RunStage(
        project_id="project",
        run_id="run",
        stage_id="transform",
        stage_type="transform",
        status="success",
        started_at=NOW,
        finished_at=NOW - timedelta(seconds=1),
    )
    source = _Source(
        _observation(future_event, stages=(invalid_stage,)),
    )
    profile = _profile(RequiredEvidenceStage("transform"))

    decision = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "event:future:timestamp_in_future" in decision.missing_evidence
    assert "stage:transform:finished_before_started" in decision.missing_evidence
    corrected_source = _Source(
        _observation(terminal, stages=(replace(invalid_stage, finished_at=NOW),))
    )
    corrected = RunReconciler(store, corrected_source).reconcile(
        "project", "run", profile, now=NOW + timedelta(minutes=3)
    )
    assert corrected.decision_id != decision.decision_id


def test_malformed_durable_event_and_stage_timestamps_are_incomplete() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    stage = RunStage(
        project_id="project",
        run_id="run",
        stage_id="transform",
        stage_type="transform",
        status="success",
        started_at=NOW,
        finished_at=NOW,
    )
    source = _Source(_observation(_event("run.terminal"), stages=(stage,)))
    profile = _profile(RequiredEvidenceStage("transform"))
    RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)
    with store._transaction() as (_, cursor):
        cursor.execute(
            "UPDATE run_event SET observed_at = ? WHERE project_id = ? AND run_id = ?",
            ("not-a-timestamp", "project", "run"),
        )
        cursor.execute(
            "UPDATE run_stage SET started_at = ? WHERE project_id = ? AND run_id = ?",
            ("not-a-timestamp", "project", "run"),
        )

    decision = RunReconciler(store, source).reconcile(
        "project", "run", profile, now=NOW + timedelta(minutes=1)
    )

    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert "event:run.terminal:invalid_timestamp" in decision.missing_evidence
    assert "stage:transform:invalid_started_at" in decision.missing_evidence


def test_required_record_families_are_evaluated_from_durable_rows() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(
            project_id="project",
            run_id="run",
            started_at=NOW - timedelta(minutes=3),
        )
    )
    store.append_artifact(
        RunArtifact(
            project_id="project", run_id="run", artifact_id="manifest", artifact_kind="manifest"
        )
    )
    profile = RequiredEvidenceProfile(
        "profile", "1", required_records=(RequiredEvidenceRecord("artifact"),)
    )
    source = _Source(_observation(_event("run.terminal")))

    decision = RunReconciler(store, source).reconcile("project", "run", profile, now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.COMPLETE


@pytest.mark.parametrize(
    ("family", "record", "required_status"),
    [
        (
            "quality_result",
            RunQualityResult(
                project_id="project",
                run_id="run",
                quality_result_id="quality",
                check_id="check",
                passed=True,
            ),
            "passed",
        ),
        (
            "catalog_change",
            RunCatalogChange(
                project_id="project",
                run_id="run",
                catalog_change_id="catalog",
                operation="merge",
                merge_outcome="merged",
            ),
            "merged",
        ),
    ],
)
def test_required_status_uses_each_record_familys_durable_field(
    family: str,
    record: RunQualityResult | RunCatalogChange,
    required_status: str,
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", started_at=NOW - timedelta(minutes=2))
    )
    if family == "quality_result":
        store.append_quality_result(record)
    else:
        store.append_catalog_change(record)
    source = _Source(_observation(_event("run.terminal")))

    decision = RunReconciler(store, source).reconcile(
        "project",
        "run",
        RequiredEvidenceProfile(
            "profile",
            "1",
            required_records=(RequiredEvidenceRecord(family, required_status=required_status),),
        ),
        now=NOW,
    )

    assert decision.evidence_completeness is EvidenceCompleteness.COMPLETE


def test_expired_artifact_is_explicitly_incomplete() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", started_at=NOW - timedelta(minutes=2))
    )
    store.append_artifact(
        RunArtifact(
            project_id="project",
            run_id="run",
            artifact_id="manifest",
            artifact_kind="manifest",
            expires_at=NOW - timedelta(seconds=1),
        )
    )
    source = _Source(_observation(_event("run.terminal")))

    decision = RunReconciler(store, source).reconcile(
        "project",
        "run",
        RequiredEvidenceProfile(
            "profile", "1", required_records=(RequiredEvidenceRecord("artifact"),)
        ),
        now=NOW,
    )

    assert decision.evidence_completeness is EvidenceCompleteness.EXPIRED
    assert "artifact:expired" in decision.missing_evidence


def test_legal_hold_prevents_artifact_expiry() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(project_id="project", run_id="run", started_at=NOW - timedelta(minutes=2))
    )
    store.append_artifact(
        RunArtifact(
            project_id="project",
            run_id="run",
            artifact_id="manifest",
            artifact_kind="manifest",
            expires_at=NOW - timedelta(seconds=1),
            legal_hold=True,
        )
    )
    source = _Source(_observation(_event("run.terminal")))

    decision = RunReconciler(store, source).reconcile(
        "project",
        "run",
        RequiredEvidenceProfile(
            "profile", "1", required_records=(RequiredEvidenceRecord("artifact"),)
        ),
        now=NOW,
    )

    assert decision.evidence_completeness is EvidenceCompleteness.COMPLETE


@pytest.mark.parametrize(
    ("family", "record"),
    [
        (
            "resource",
            RunResource(project_id="project", run_id="run", resource_id="r1"),
        ),
        (
            "catalog_change",
            RunCatalogChange(
                project_id="project", run_id="run", catalog_change_id="c1", operation="write"
            ),
        ),
        (
            "quality_result",
            RunQualityResult(
                project_id="project", run_id="run", quality_result_id="q1", check_id="check"
            ),
        ),
        (
            "artifact",
            RunArtifact(project_id="project", run_id="run", artifact_id="a1", artifact_kind="log"),
        ),
    ],
)
def test_required_records_cannot_satisfy_a_later_attempt(
    family: str, record: RunResource | RunCatalogChange | RunQualityResult | RunArtifact
) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run", attempt=1))
    store_record = replace(record, attempt=1)
    {
        "resource": store.append_resource,
        "catalog_change": store.append_catalog_change,
        "quality_result": store.append_quality_result,
        "artifact": store.append_artifact,
    }[family](store_record)
    source = _Source(_observation(_event("run.terminal", attempt=2), attempt=2))

    decision = RunReconciler(store, source).reconcile(
        "project",
        "run",
        RequiredEvidenceProfile("profile", "1", required_records=(RequiredEvidenceRecord(family),)),
        now=NOW,
    )

    assert decision.evidence_completeness is EvidenceCompleteness.INCOMPLETE
    assert f"{family}:minimum:1" in decision.missing_evidence


def test_cross_run_duplicate_event_conflict_is_deterministic_without_forged_parent() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run"))
    event = _event("run.start", event_id="stable")
    store.append_event(event)

    with pytest.raises(IdempotencyConflict, match="correlated to another run"):
        store.append_event(replace(event, run_id="forged"))


def test_same_stage_id_across_attempts_is_rejected_as_unscoped() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run"))
    store.append_stage(
        RunStage(
            project_id="project", run_id="run", stage_id="stage", attempt=1, stage_type="transform"
        )
    )

    with pytest.raises(IdempotencyConflict):
        store.append_stage(
            RunStage(
                project_id="project",
                run_id="run",
                stage_id="stage",
                attempt=2,
                stage_type="transform",
            )
        )


def test_reconciliation_rejects_existing_stage_reference_from_another_attempt() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(PipelineRun(project_id="project", run_id="run", attempt=1))
    store.append_stage(RunStage(project_id="project", run_id="run", stage_id="stage", attempt=1))
    event = replace(_event("run.terminal", attempt=2), stage_id="stage")
    observation = _observation(event, attempt=2, stages=())

    with pytest.raises(ValueError, match="stage .* has attempt 1, expected 2"):
        store.reconcile_observation(
            observation,
            _profile(),
            now=NOW,
            stale_after=None,
        )

    assert store.count_events("project", "run") == 0
    assert store.get_run("project", "run")["attempt"] == 1


@pytest.mark.parametrize(
    "state",
    [EvidenceCompleteness.EXPIRED, EvidenceCompleteness.REDACTED],
)
def test_unavailable_required_evidence_never_becomes_complete(state: EvidenceCompleteness) -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal"), evidence_state=state))

    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert decision.evidence_completeness is state
    assert store.get_run("project", "run")["evidence_completeness"] == state.value


def test_missing_source_evidence_is_durable_when_the_parent_already_exists() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    store.append_pipeline_run(
        PipelineRun(
            project_id="project",
            run_id="run",
            started_at=NOW - timedelta(minutes=3),
        )
    )
    source = _Source(
        _observation(_event("run.terminal"), evidence_state=EvidenceCompleteness.MISSING)
    )

    decision = RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert decision.evidence_completeness is EvidenceCompleteness.MISSING
    assert store.get_run("project", "run")["evidence_completeness"] == "missing"


def test_missing_evidence_outcome_is_distinct_from_authoritative_absence() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(
        _observation(_event("run.terminal"), evidence_state=EvidenceCompleteness.MISSING)
    )

    RunReconciler(store, source).reconcile("project", "run", _profile(), now=NOW)

    assert store.count_events("project", "run") == 1


def test_concurrent_duplicate_scans_persist_one_decision() -> None:
    store = SQLiteRunEvidenceStore(":memory:")
    source = _Source(_observation(_event("run.terminal")))
    reconciler = RunReconciler(store, source)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: reconciler.reconcile("project", "run", _profile(), now=NOW), range(2)
            )
        )

    assert results[0].decision_id == results[1].decision_id
    assert len(store.list_reconciliation_decisions("project", "run")) == 1
