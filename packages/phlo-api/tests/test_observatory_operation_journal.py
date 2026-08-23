"""Tests the Observatory operation journal and durable-state storage.

Focuses on contended writes: forced-concurrent saved-query and journal
appends must both survive via the durable-state lock, and corrupt backing
files surface as StorageCorruptionError instead of silent data loss.
"""

from __future__ import annotations

from pathlib import Path
from threading import Barrier, Thread

import pytest

from phlo_api.observatory_api.observatory_models import (
    ObservatoryAction,
    ObservatoryActionResult,
    ObservatoryHealth,
    ObservatoryOperation,
    ObservatoryResourceRef,
    ObservatorySavedQueryRequest,
)
from phlo_api.observatory_api.observatory_operation_journal import (
    append_operation,
    build_operation_observability_context,
    load_operation_journal,
    operation_from_action_result,
    record_action_result,
)
from phlo_api.observatory_api.observatory_saved_queries import (
    load_saved_queries,
    save_query,
    saved_queries_path,
)
from phlo_api.observatory_api.observatory_durable_state import state_namespace
from phlo.plugins.observatory_settings import (
    SettingsScope,
    StorageCorruptionError,
    get_settings_service,
)


@pytest.fixture(autouse=True)
def use_memory_settings_store(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    from phlo.plugins.observatory_settings import _reset_memory_service

    _reset_memory_service()


def test_forced_concurrent_writes_preserve_saved_queries_and_journal_records(
    tmp_path: Path,
) -> None:
    # Release all four writers at once so the durable-state lock is genuinely
    # contended rather than hit sequentially.
    barrier = Barrier(4)
    failures: list[BaseException] = []

    def save(name: str) -> None:
        try:
            barrier.wait()
            save_query(
                tmp_path,
                ObservatorySavedQueryRequest(name=name, sql="select * from raw.orders limit 1"),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    def append(record_id: str) -> None:
        try:
            barrier.wait()
            append_operation(
                tmp_path,
                _operation(record_id),
                record_id=record_id,
                recorded_at="2026-05-16T12:00:00+00:00",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [
        Thread(target=save, args=("one",)),
        Thread(target=save, args=("two",)),
        Thread(target=append, args=("op-one",)),
        Thread(target=append, args=("op-two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert {query.name for query in load_saved_queries(tmp_path)} == {"one", "two"}
    assert {operation.id for operation in load_operation_journal(tmp_path)} == {"op-one", "op-two"}


def test_migrates_valid_legacy_json_once_without_cross_project_state(tmp_path: Path) -> None:
    legacy_path = saved_queries_path(tmp_path)
    legacy_bytes = b'{\n  "items": [{"id":"legacy","name":"Legacy","sql":"select * from raw.orders limit 1","branch":null,"created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00","metadata":{}}]\n}\n'
    legacy_path.write_bytes(legacy_bytes)

    migrated = load_saved_queries(tmp_path)
    other_project = tmp_path / "other"

    assert [query.id for query in migrated] == ["legacy"]
    assert legacy_path.read_bytes() == legacy_bytes
    assert load_saved_queries(other_project) == []
    service = get_settings_service()
    assert service.get(SettingsScope.GLOBAL, state_namespace(tmp_path, "saved_queries")) is not None
    assert (
        service.get(SettingsScope.GLOBAL, state_namespace(other_project, "saved_queries"))
        is not None
    )


def test_malformed_legacy_json_is_preserved_and_never_replaced(tmp_path: Path) -> None:
    path = saved_queries_path(tmp_path)
    original = b"{ definitely not json"
    path.write_bytes(original)

    with pytest.raises(StorageCorruptionError, match="durable state is unavailable"):
        load_saved_queries(tmp_path)
    with pytest.raises(StorageCorruptionError, match="durable state is unavailable"):
        save_query(
            tmp_path,
            ObservatorySavedQueryRequest(name="new", sql="select * from raw.orders limit 1"),
        )

    assert path.read_bytes() == original


def _operation(record_id: str) -> ObservatoryOperation:
    return ObservatoryOperation(
        id=record_id,
        name=record_id,
        kind="test",
        status="succeeded",
        health=ObservatoryHealth(state="ok"),
    )


def test_append_operation_persists_newest_record_first(tmp_path: Path) -> None:
    first = ObservatoryOperation(
        id="service:restart",
        name="Restart",
        kind="service.restart",
        status="succeeded",
        health=ObservatoryHealth(state="ok", message="done"),
        target=ObservatoryResourceRef(kind="service", id="phlo-api", label="phlo-api"),
        metadata={"unsafe_url": "http://internal", "safe_count": 1},
    )
    second = first.model_copy(update={"id": "service:stop", "name": "Stop"})

    append_operation(
        tmp_path,
        first,
        record_id="op-first",
        recorded_at="2026-05-16T10:00:00+00:00",
    )
    append_operation(
        tmp_path,
        second,
        record_id="op-second",
        recorded_at="2026-05-16T10:01:00+00:00",
    )

    records = load_operation_journal(tmp_path)

    assert [record.id for record in records] == ["op-second", "op-first"]
    assert records[0].metadata["original_operation_id"] == "service:stop"
    assert records[1].metadata["safe_count"] == 1
    assert "unsafe_url" not in records[1].metadata


def test_operation_from_action_result_creates_skipped_operation() -> None:
    action = ObservatoryAction(
        id="quality:raw.orders:rerun",
        label="Re-run quality check",
        kind="quality.rerun",
        enabled=False,
        reason="Quality re-runs need a provider-backed execution contract.",
        required_capability="quality_backend",
    )
    result = ObservatoryActionResult(
        action=action,
        status="skipped",
        message="Quality re-runs need a provider-backed execution contract.",
    )

    operation = operation_from_action_result(
        result,
        target=ObservatoryResourceRef(kind="quality", id="raw.orders", label="raw.orders"),
    )

    assert operation.id == "quality:raw.orders:rerun"
    assert operation.name == "Re-run quality check"
    assert operation.kind == "quality.rerun"
    assert operation.status == "skipped"
    assert operation.health.state == "warning"
    assert operation.target is not None
    assert operation.target.kind == "quality"
    assert operation.metadata["action_id"] == "quality:raw.orders:rerun"


def test_record_action_result_returns_result_with_recorded_operation(tmp_path: Path) -> None:
    action = ObservatoryAction(
        id="unsupported:thing",
        label="Unsupported action",
        kind="unsupported",
        enabled=False,
    )
    result = ObservatoryActionResult(
        action=action,
        status="failed",
        message="Unsupported Observatory action: unsupported:thing",
    )

    recorded_result = record_action_result(
        tmp_path,
        result,
        record_id="op-recorded",
        recorded_at="2026-05-16T11:00:00+00:00",
    )

    assert recorded_result.operation is not None
    assert recorded_result.operation.id == "op-recorded"
    assert recorded_result.operation.status == "failed"
    assert load_operation_journal(tmp_path)[0].id == "op-recorded"


def test_append_operation_adds_stable_observability_identifiers(tmp_path: Path) -> None:
    operation = ObservatoryOperation(
        id="workflow:apply:raw-orders",
        name="Apply workflow proposal",
        kind="workflow.apply",
        status="succeeded",
        health=ObservatoryHealth(state="ok"),
        target=ObservatoryResourceRef(kind="workflow", id="raw-orders", label="raw-orders"),
        metadata={
            "trace_id": "trace-123",
            "log_id": "log-456",
            "metric_id": "metric-789",
            "incident_id": "incident-001",
        },
    )

    recorded = append_operation(
        tmp_path,
        operation,
        record_id="op-recorded",
        recorded_at="2026-05-16T12:00:00+00:00",
    )

    assert recorded.metadata["observability_contract"]["operation_id"] == "op-recorded"
    assert recorded.metadata["observability_contract"]["original_operation_id"] == (
        "workflow:apply:raw-orders"
    )
    assert recorded.metadata["observability_contract"]["trace_ids"] == ["trace-123"]
    assert recorded.metadata["observability_contract"]["log_ids"] == ["log-456"]
    assert recorded.metadata["observability_contract"]["metric_ids"] == ["metric-789"]
    assert recorded.metadata["observability_contract"]["incident_ids"] == ["incident-001"]


def test_build_operation_observability_context_is_agent_readable() -> None:
    operation = ObservatoryOperation(
        id="op-recorded",
        name="Apply workflow proposal",
        kind="workflow.apply",
        status="failed",
        health=ObservatoryHealth(state="error", message="Validation failed"),
        target=ObservatoryResourceRef(kind="workflow", id="raw-orders", label="raw-orders"),
        metadata={
            "observability_contract": {
                "operation_id": "op-recorded",
                "trace_ids": ["trace-123"],
                "log_ids": ["log-456"],
                "metric_ids": ["metric-789"],
                "incident_ids": ["incident-001"],
            },
            "message": "Validation failed",
        },
    )

    context = build_operation_observability_context(operation)

    assert context["schema_version"] == "phlo.operation_observability.v1"
    assert context["operation"]["id"] == "op-recorded"
    assert context["identifiers"] == {
        "operation_id": "op-recorded",
        "trace_ids": ["trace-123"],
        "log_ids": ["log-456"],
        "metric_ids": ["metric-789"],
        "incident_ids": ["incident-001"],
    }
    assert context["incident"]["status"] == "open"
    assert context["incident"]["severity"] == "error"
    assert context["retention"]["history_limit"] == 200
