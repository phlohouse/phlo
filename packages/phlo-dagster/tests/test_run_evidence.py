"""Verify DagsterRunEvidenceSource resolves runs from the Dagster instance,
including paginated event-record fetching, into the shared evidence store."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from phlo.run_evidence import (
    RequiredEvidenceProfile,
    RunEvidenceUnavailable,
    RunReconciler,
    SQLiteRunEvidenceStore,
)
from phlo_dagster.run_evidence import DagsterRunEvidenceSource


class _Instance:
    def __init__(self, run, records, *, page_size=None):
        self.run = run
        self.records = records
        self.page_size = page_size

    def get_run_by_id(self, run_id):
        return self.run if self.run and self.run.run_id == run_id else None

    def get_run_record_by_id(self, run_id):
        if self.run is None:
            return None
        return SimpleNamespace(
            dagster_run=self.run,
            start_time=self.run.start_time,
            end_time=self.run.end_time,
            update_timestamp=self.run.update_timestamp,
        )

    def get_records_for_run(self, *, run_id, ascending, cursor=None):
        if self.page_size is None:
            return SimpleNamespace(records=self.records)
        offset = int(cursor or 0)
        page = self.records[offset : offset + self.page_size]
        next_offset = offset + len(page)
        return SimpleNamespace(
            records=page,
            cursor=str(next_offset),
            has_more=next_offset < len(self.records),
        )


def _run(status="SUCCESS", *, attempt="2", run_id="dagster-run", parent_run_id=None, tags=None):
    run_tags = {"phlo/attempt": attempt}
    if tags:
        run_tags.update(tags)
    return SimpleNamespace(
        run_id=run_id,
        job_name="orders",
        status=SimpleNamespace(value=status),
        tags=run_tags,
        parent_run_id=parent_run_id
        if parent_run_id is not None
        else ("parent" if attempt == "" else None),
        root_run_id="root-run",
        start_time=1783944000.0,
        end_time=1783944060.0 if status == "SUCCESS" else None,
        update_timestamp=1783944060.0,
    )


def _record(storage_id, event_type, *, asset=None, step_key=None, message="event", timestamp=None):
    dagster_event = SimpleNamespace(
        asset_key=SimpleNamespace(path=asset.split("/") if asset else None)
        if asset
        else SimpleNamespace(asset_key=None)
    )
    return SimpleNamespace(
        storage_id=storage_id,
        timestamp=timestamp if timestamp is not None else 1783944050.0 + storage_id,
        event_type=SimpleNamespace(value=event_type),
        event_log_entry=SimpleNamespace(
            timestamp=timestamp if timestamp is not None else 1783944050.0 + storage_id,
            event_type=SimpleNamespace(value=event_type),
            message=message,
            step_key=step_key,
            dagster_event=dagster_event,
        ),
    )


def test_dagster_source_maps_durable_run_and_event_log_records() -> None:
    source = DagsterRunEvidenceSource(
        _Instance(
            _run(),
            [_record(10, "RUN_START"), _record(11, "ASSET_MATERIALIZATION", asset="raw/orders")],
        ),
        project_id="project",
    )

    observation = source.observe_run("project", "dagster-run")

    assert observation is not None
    assert observation.status == "success"
    assert observation.attempt == 2
    assert observation.pipeline_name == "orders"
    assert [event.event_id for event in observation.events] == ["dagster-run:10", "dagster-run:11"]
    assert observation.events[1].event_type == "stage.materialization"
    assert observation.events[1].stage_id == observation.stages[0].stage_id
    assert observation.events[1].payload["stage_id"] == observation.stages[0].stage_id
    assert observation.stages[0].asset == "raw.orders"
    assert observation.stages[0].provider == "dagster"
    for item in (*observation.events, *observation.stages):
        assert item.resource_ref is not None
        assert item.resource_ref.resource_type == "run"
        assert item.resource_ref.resource_id == "root-run"
        assert item.resource_ref.tenant == "project"
        assert item.resource_ref.attributes == {"attempt": "2"}


def test_dagster_source_requires_injected_project_identity() -> None:
    source = DagsterRunEvidenceSource(_Instance(_run(), []), project_id="project")

    with pytest.raises(ValueError, match="another project"):
        source.observe_run("other-project", "dagster-run")


def test_dagster_source_marks_authoritative_missing_run_without_inventing_failure() -> None:
    source = DagsterRunEvidenceSource(_Instance(None, []), project_id="project")

    observation = source.observe_run("project", "unknown")

    assert observation is not None
    assert observation.evidence_state.value == "missing"
    assert observation.status is None


def test_success_with_no_materializations_is_not_inferred_as_no_data() -> None:
    source = DagsterRunEvidenceSource(
        _Instance(_run(), [_record(10, "RUN_SUCCESS")]), project_id="project"
    )

    observation = source.observe_run("project", "dagster-run")

    assert observation.status == "success"
    assert all(event.event_type != "run.no_data" for event in observation.events)


def test_dagster_source_consumes_all_event_log_pages() -> None:
    source = DagsterRunEvidenceSource(
        _Instance(
            _run(),
            [
                _record(10, "RUN_START"),
                _record(11, "RUN_SUCCESS"),
                _record(12, "STEP_SUCCESS", step_key="orders"),
            ],
            page_size=1,
        ),
        project_id="project",
    )

    observation = source.observe_run("project", "dagster-run")

    assert [event.event_id for event in observation.events] == [
        "dagster-run:10",
        "dagster-run:11",
        "dagster-run:12",
    ]
    assert observation.stages[-1].status == "success"


def test_dagster_stage_identity_includes_distinct_asset_checks() -> None:
    first = _record(10, "ASSET_CHECK_EVALUATION", asset="raw/orders")
    second = _record(11, "ASSET_CHECK_EVALUATION", asset="raw/orders")
    first.event_log_entry.dagster_event.asset_check_evaluation = SimpleNamespace(
        check_name="freshness", passed=True
    )
    second.event_log_entry.dagster_event.asset_check_evaluation = SimpleNamespace(
        check_name="volume", passed=True
    )

    observation = DagsterRunEvidenceSource(
        _Instance(_run(), [first, second]), project_id="project"
    ).observe_run("project", "dagster-run")

    assert len({stage.stage_id for stage in observation.stages}) == 2
    assert observation.events[0].payload["check_identity"] == "freshness"
    assert observation.events[1].payload["check_identity"] == "volume"


@pytest.mark.parametrize(
    "connection",
    [
        SimpleNamespace(records=[_record(10, "RUN_SUCCESS")], has_more=True, cursor=None),
        SimpleNamespace(records=[_record(10, "RUN_SUCCESS")], has_more=True, cursor="0"),
    ],
)
def test_dagster_non_advancing_pagination_is_unavailable_without_reconciliation_mutation(
    connection,
) -> None:
    class BrokenPaginationInstance(_Instance):
        def get_records_for_run(self, **kwargs):
            return connection

    source = DagsterRunEvidenceSource(BrokenPaginationInstance(_run(), []), project_id="project")
    store = SQLiteRunEvidenceStore(":memory:")

    with pytest.raises(RunEvidenceUnavailable):
        RunReconciler(store, source).reconcile(
            "project", "dagster-run", RequiredEvidenceProfile("profile", "1")
        )
    assert store.get_run("project", "dagster-run") is None


def test_dagster_repeated_pagination_cursor_is_unavailable() -> None:
    class CyclingPaginationInstance(_Instance):
        def __init__(self):
            super().__init__(_run(), [])
            self.pages = {
                None: SimpleNamespace(records=[], has_more=True, cursor="a"),
                "a": SimpleNamespace(records=[], has_more=True, cursor="b"),
                "b": SimpleNamespace(records=[], has_more=True, cursor="a"),
            }

        def get_records_for_run(self, *, cursor=None, **kwargs):
            return self.pages[cursor]

    source = DagsterRunEvidenceSource(CyclingPaginationInstance(), project_id="project")

    with pytest.raises(RunEvidenceUnavailable, match="cursor repeated"):
        source.observe_run("project", "dagster-run")


def test_dagster_retry_chain_missing_parent_is_unavailable() -> None:
    run = _run(attempt="", parent_run_id="missing")
    source = DagsterRunEvidenceSource(_Instance(run, []), project_id="project")

    with pytest.raises(RunEvidenceUnavailable, match="parent was missing"):
        source.observe_run("project", run.run_id)


def test_explicit_attempt_tag_still_validates_declared_parent() -> None:
    run = _run(attempt="2", parent_run_id="missing")
    source = DagsterRunEvidenceSource(_Instance(run, []), project_id="project")

    with pytest.raises(RunEvidenceUnavailable, match="parent was missing"):
        source.observe_run("project", run.run_id)


@pytest.mark.parametrize("tagged_attempt", ["1", "3"])
def test_explicit_attempt_tag_must_match_known_parent_chain(tagged_attempt: str) -> None:
    root = _run(attempt="1", run_id="root", parent_run_id=None)
    child = _run(attempt=tagged_attempt, run_id="child", parent_run_id="root")

    class ChainInstance(_Instance):
        def __init__(self):
            super().__init__(child, [])
            self.runs = {"root": root, "child": child}

        def get_run_by_id(self, run_id):
            return self.runs.get(run_id)

    source = DagsterRunEvidenceSource(ChainInstance(), project_id="project")

    with pytest.raises(RunEvidenceUnavailable, match="disagreed"):
        source.observe_run("project", "child")


def test_explicit_attempt_tag_matching_parent_chain_is_accepted() -> None:
    root = _run(attempt="1", run_id="root", parent_run_id=None)
    child = _run(attempt="2", run_id="child", parent_run_id="root")

    class ChainInstance(_Instance):
        def __init__(self):
            super().__init__(child, [])
            self.runs = {"root": root, "child": child}

        def get_run_by_id(self, run_id):
            return self.runs.get(run_id)

    observation = DagsterRunEvidenceSource(ChainInstance(), project_id="project").observe_run(
        "project", "child"
    )

    assert observation.attempt == 2


def test_dagster_retry_chain_cycle_is_unavailable() -> None:
    first = _run(attempt="", run_id="first", parent_run_id="second")
    second = _run(attempt="", run_id="second", parent_run_id="first")

    class CycleInstance(_Instance):
        def __init__(self):
            super().__init__(first, [])
            self.runs = {"first": first, "second": second}

        def get_run_by_id(self, run_id):
            return self.runs.get(run_id)

    source = DagsterRunEvidenceSource(CycleInstance(), project_id="project")

    with pytest.raises(RunEvidenceUnavailable, match="cycle"):
        source.observe_run("project", "first")


def test_dagster_retry_attempt_traverses_the_full_parent_chain() -> None:
    root = _run(attempt="1", run_id="root", parent_run_id=None)
    parent = _run(attempt="", run_id="parent", parent_run_id="root")
    child = _run(attempt="", run_id="child", parent_run_id="parent")

    class ChainInstance(_Instance):
        def __init__(self):
            super().__init__(child, [])
            self.runs = {"root": root, "parent": parent, "child": child}

        def get_run_by_id(self, run_id):
            return self.runs.get(run_id)

    source = DagsterRunEvidenceSource(ChainInstance(), project_id="project")

    observation = source.observe_run("project", "child")

    assert observation.attempt == 3
    assert observation.run_id == "root-run"
    assert observation.provider_run_id == "child"


def test_quiet_running_dagster_run_has_no_invented_heartbeat() -> None:
    source = DagsterRunEvidenceSource(_Instance(_run(status="STARTED"), []), project_id="project")

    observation = source.observe_run("project", "dagster-run")

    assert observation.status == "started"
    assert observation.heartbeat_at is None


def test_missing_event_timestamp_is_skipped_instead_of_using_now() -> None:
    source = DagsterRunEvidenceSource(
        _Instance(_run(), [_record(10, "RUN_SUCCESS")]), project_id="project"
    )
    source.instance.records[0].event_log_entry.timestamp = None

    observation = source.observe_run("project", "dagster-run")

    assert observation.events == ()


def test_dagster_message_is_bounded_redacted_and_checksummed() -> None:
    source = DagsterRunEvidenceSource(
        _Instance(
            _run(),
            [_record(10, "RUN_SUCCESS", message="password=super-secret token=abc")],
        ),
        project_id="project",
    )

    event = source.observe_run("project", "dagster-run").events[0]

    assert "super-secret" not in str(event.payload)
    assert "abc" not in str(event.payload)
    assert event.payload["message_checksum"]


def test_materializations_are_partition_and_record_identity_scoped() -> None:
    run = _run(tags={"dagster/partition": "2026-07-13"})
    source = DagsterRunEvidenceSource(
        _Instance(
            run,
            [
                _record(10, "ASSET_MATERIALIZATION", asset="raw/orders"),
                _record(11, "ASSET_MATERIALIZATION", asset="raw/orders"),
            ],
        ),
        project_id="project",
    )

    observation = source.observe_run("project", "dagster-run")

    assert len({stage.stage_id for stage in observation.stages}) == 2


def test_failed_step_and_cancellation_are_mapped_explicitly() -> None:
    failed = DagsterRunEvidenceSource(
        _Instance(
            _run(status="FAILURE"),
            [_record(10, "RUN_FAILURE"), _record(11, "STEP_FAILURE", step_key="orders")],
        ),
        project_id="project",
    ).observe_run("project", "dagster-run")
    cancelled = DagsterRunEvidenceSource(
        _Instance(_run(status="CANCELED"), [_record(10, "RUN_CANCELED")]), project_id="project"
    ).observe_run("project", "dagster-run")

    assert failed.status == "failed"
    assert failed.stages[0].status == "failed"
    assert cancelled.status == "cancelled"


def test_explicit_no_data_tag_is_preserved_without_inference() -> None:
    source = DagsterRunEvidenceSource(
        _Instance(_run(tags={"phlo/no_data": "true"}), [_record(10, "RUN_SUCCESS")]),
        project_id="project",
    )

    observation = source.observe_run("project", "dagster-run")

    assert observation.status == "no_data"
    terminal = next(event for event in observation.events if event.event_type == "run.terminal")
    assert terminal.payload["status"] == "no_data"
    assert terminal.payload["provider_status"] == "success"
    assert any(event.event_type == "run.no_data" for event in observation.events)


@pytest.mark.parametrize("status", ["FAILURE", "CANCELED", "STARTED"])
def test_no_data_tag_does_not_override_non_successful_dagster_status(status: str) -> None:
    source = DagsterRunEvidenceSource(
        _Instance(_run(status=status, tags={"phlo/no_data": "true"}), []),
        project_id="project",
    )

    observation = source.observe_run("project", "dagster-run")

    assert observation.status == {"FAILURE": "failed", "CANCELED": "cancelled"}.get(
        status, "started"
    )
    assert all(event.event_type != "run.no_data" for event in observation.events)


@pytest.mark.parametrize("status", ["FAILURE", "CANCELED"])
def test_no_data_tag_does_not_override_terminal_status_with_stale_success_event(
    status: str,
) -> None:
    source = DagsterRunEvidenceSource(
        _Instance(
            _run(status=status, tags={"phlo/no_data": "true"}),
            [_record(10, "RUN_SUCCESS")],
        ),
        project_id="project",
    )

    observation = source.observe_run("project", "dagster-run")

    assert observation.status == {"FAILURE": "failed", "CANCELED": "cancelled"}[status]
    assert all(event.event_type != "run.no_data" for event in observation.events)


def test_provider_outage_is_distinct_from_authoritative_absence() -> None:
    class DownInstance(_Instance):
        def get_run_by_id(self, run_id):
            raise TimeoutError("down")

    source = DagsterRunEvidenceSource(DownInstance(None, []), project_id="project")

    with pytest.raises(RunEvidenceUnavailable):
        source.observe_run("project", "dagster-run")


def test_local_sqlite_runs_have_independent_replay_safe_event_ids(tmp_path):
    from dagster import DagsterInstance, job, op

    @op
    def emit_value():
        return 1

    @job
    def example():
        emit_value()

    (tmp_path / "dagster").mkdir()
    with DagsterInstance.local_temp(
        str(tmp_path / "dagster"), overrides={"telemetry": {"enabled": False}}
    ) as instance:
        runs = [example.execute_in_process(instance=instance) for _ in range(2)]
        records = [instance.get_records_for_run(run.run_id).records for run in runs]
        assert {r.storage_id for r in records[0]} & {r.storage_id for r in records[1]}
        store = SQLiteRunEvidenceStore(str(tmp_path / "evidence.sqlite"))
        source = DagsterRunEvidenceSource(instance, project_id="project")
        reconciler = RunReconciler(store, source)
        for run in runs:
            for _ in range(2):
                reconciler.reconcile("project", run.run_id, RequiredEvidenceProfile("profile", "1"))
            assert store.count_events("project", run.run_id) == len(
                source.observe_run("project", run.run_id).events
            )


def test_legacy_bare_event_replay_preserves_stage_and_terminal_report():
    from dataclasses import replace
    from datetime import UTC, datetime
    from phlo.run_evidence import build_run_report

    source = DagsterRunEvidenceSource(
        _Instance(
            _run(),
            [
                _record(10, "ASSET_MATERIALIZATION", asset="raw/orders"),
                _record(11, "RUN_SUCCESS"),
            ],
        ),
        project_id="project",
    )
    observation = source.observe_run("project", "dagster-run")
    legacy = replace(
        observation,
        events=tuple(
            replace(event, event_id=str(event.payload["storage_id"]))
            for event in observation.events
        ),
    )
    store = SQLiteRunEvidenceStore(":memory:")
    profile = RequiredEvidenceProfile("profile", "1")
    store.reconcile_observation(legacy, profile, now=datetime.now(UTC), stale_after=None)
    before = build_run_report(store, "project", "root-run", 2)
    for _ in range(2):
        RunReconciler(store, source).reconcile("project", "dagster-run", profile)
    after = build_run_report(store, "project", "root-run", 2)
    assert after.stages == before.stages
    assert after.terminal_outcome.status == before.terminal_outcome.status == "success"
    assert store.count_events("project", "root-run") == 4
    events = store.list_events("project", "root-run")
    for legacy_id in ("10", "11"):
        checksums = {
            row["payload_checksum"]
            for row in events
            if row["event_id"] in {legacy_id, f"dagster-run:{legacy_id}"}
        }
        assert len(checksums) == 1
