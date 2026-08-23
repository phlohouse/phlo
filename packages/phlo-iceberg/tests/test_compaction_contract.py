"""Provider-neutral contract tests for Iceberg table compaction.

Pins dry-run planning, executor identity passing, ref overrides, and
fail-closed outcome handling where any failure reports outcome unknown and is
not retry-safe.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from phlo.capabilities import (
    MaintenanceExecutionError,
    MaintenanceExecutionPhase,
    MaintenanceTableStore,
)
import phlo_iceberg.resource as resource_module
from phlo_iceberg.resource import IcebergResource


def _resource(monkeypatch, metadata):
    resource = IcebergResource(ref="main")
    values = iter(metadata)
    monkeypatch.setattr(resource, "_compaction_metadata", lambda table_name, ref: next(values))
    return resource


def test_dry_run_reports_plan_without_executor(monkeypatch) -> None:
    resource = _resource(monkeypatch, [(41, {"file_count": 4, "snapshot_count": 2})])

    result = resource.compact(table_name="raw.events", dry_run=True, operation_id="run-41")

    assert result["status"] == "planned"
    assert result["executed"] is False
    assert result["before_revision"] == 41
    assert result["before_snapshot_id"] == 41
    assert result["planned"]["trino_boundary"] == "not_invoked"
    assert result["retry_safe"] is True


def test_iceberg_resource_satisfies_neutral_maintenance_table_store_contract() -> None:
    assert isinstance(IcebergResource(ref="main"), MaintenanceTableStore)


def test_execute_passes_snapshot_and_operation_identity_to_executor(monkeypatch) -> None:
    resource = _resource(
        monkeypatch,
        [
            (41, {"file_count": 4, "snapshot_count": 2, "total_size_mb": 4.0}),
            (41, {"file_count": 4, "snapshot_count": 2, "total_size_mb": 4.0}),
            (42, {"file_count": 1, "snapshot_count": 3, "total_size_mb": 4.0}),
        ],
    )
    # compact() reads metadata three times: before execution, immediately
    # before submitting to the executor (the snapshot must still be 41), and
    # after execution (advanced to 42 by the compaction).
    executor = MagicMock()
    executor.compact_table.return_value = {
        "catalog": "iceberg",
        "ref": "main",
        "sql": 'ALTER TABLE "raw"."events" EXECUTE optimize',
    }

    result = resource.compact(
        table_name="raw.events",
        expected_snapshot_id=41,
        operation_id="run-41",
        executor=executor,
    )

    executor.compact_table.assert_called_once_with(
        table_name="raw.events",
        ref="main",
        expected_revision=41,
        operation_id="run-41",
    )
    assert result["status"] == "succeeded"
    assert result["before_revision"] == 41
    assert result["after_revision"] == 42
    assert result["before_snapshot_id"] == 41
    assert result["after_snapshot_id"] == 42
    assert result["affected"]["file_count_before"] == 4
    assert result["affected"]["file_count_after"] == 1
    assert result["evidence"]["before"]["snapshot_count"] == 2
    assert result["evidence"]["after"]["snapshot_count"] == 3
    assert result["evidence"]["provider"] == {
        "catalog": "iceberg",
        "ref": "main",
        "sql": 'ALTER TABLE "raw"."events" EXECUTE optimize',
    }
    assert result["retry_safe"] is True


def test_execute_honors_override_ref(monkeypatch) -> None:
    resource = _resource(
        monkeypatch,
        [
            (41, {"file_count": 2, "snapshot_count": 1}),
            (41, {"file_count": 2, "snapshot_count": 1}),
            (42, {"file_count": 1, "snapshot_count": 2}),
        ],
    )
    executor = MagicMock()

    result = resource.compact(
        table_name="raw.events",
        override_ref="dev",
        expected_snapshot_id=41,
        executor=executor,
    )

    assert result["ref"] == "dev"
    assert executor.compact_table.call_args.kwargs["ref"] == "dev"


def test_execute_failure_is_outcome_unknown_and_not_retry_safe(monkeypatch) -> None:
    resource = _resource(
        monkeypatch,
        [
            (41, {"file_count": 4, "snapshot_count": 2}),
            (41, {"file_count": 4, "snapshot_count": 2}),
        ],
    )
    executor = MagicMock()
    executor.compact_table.side_effect = MaintenanceExecutionError(
        MaintenanceExecutionPhase.SUBMISSION,
        RuntimeError("connection reset"),
    )

    result = cast(
        dict[str, Any],
        resource.compact(
            table_name="raw.events",
            expected_snapshot_id=41,
            executor=executor,
        ),
    )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "maintenance_outcome_unknown"
    assert result["failure"]["outcome"] == "unknown"
    assert result["failure"]["phase"] == "submission"
    assert result["failure"]["retryable"] is False
    assert result["executed"] is True
    assert result["retry_safe"] is False


def test_preflight_failure_is_not_reported_as_executed(monkeypatch) -> None:
    resource = _resource(
        monkeypatch,
        [
            (41, {"file_count": 4, "snapshot_count": 2}),
            (41, {"file_count": 4, "snapshot_count": 2}),
        ],
    )
    executor = MagicMock()
    executor.compact_table.side_effect = MaintenanceExecutionError(
        MaintenanceExecutionPhase.PREFLIGHT,
        RuntimeError("connection refused"),
    )

    result = resource.compact(
        table_name="raw.events",
        expected_snapshot_id=41,
        executor=executor,
    )

    assert result["failure"]["code"] == "maintenance_preflight_failed"
    assert result["failure"]["outcome"] == "not_submitted"
    assert result["failure"]["retryable"] is True
    assert result["accepted"] is False
    assert result["executed"] is False
    assert result["retry_safe"] is True


def test_generic_provider_failure_is_outcome_unknown_and_not_retryable(monkeypatch) -> None:
    resource = _resource(
        monkeypatch,
        [
            (41, {"file_count": 4, "snapshot_count": 2}),
            (41, {"file_count": 4, "snapshot_count": 2}),
        ],
    )
    executor = MagicMock()
    executor.compact_table.side_effect = RuntimeError("connection reset")

    result = cast(
        dict[str, Any],
        resource.compact(
            table_name="raw.events",
            expected_snapshot_id=41,
            executor=executor,
        ),
    )

    assert result["failure"]["code"] == "maintenance_outcome_unknown"
    assert result["failure"]["retryable"] is False
    assert result["retry_safe"] is False


def test_post_execution_metadata_failure_is_outcome_unknown_and_not_retryable(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    metadata = MagicMock(
        side_effect=[
            (41, {"file_count": 4, "snapshot_count": 2}),
            (41, {"file_count": 4, "snapshot_count": 2}),
            RuntimeError("connection reset"),
        ]
    )
    monkeypatch.setattr(resource, "_compaction_metadata", metadata)
    executor = MagicMock()

    result = cast(
        dict[str, Any],
        resource.compact(
            table_name="raw.events",
            expected_snapshot_id=41,
            executor=executor,
        ),
    )

    assert result["failure"]["code"] == "maintenance_outcome_unknown"
    assert result["failure"]["retryable"] is False
    assert result["retry_safe"] is False


def test_compaction_metadata_uses_one_loaded_table_for_snapshot_and_stats(monkeypatch) -> None:
    resource = IcebergResource(ref="main")
    catalog = MagicMock()
    table = MagicMock()
    table.current_snapshot.return_value = SimpleNamespace(snapshot_id=41)
    catalog.load_table.return_value = table
    stats = {"current_snapshot_id": 41, "file_count": 2, "snapshot_count": 1}
    get_stats = MagicMock(return_value=stats)
    monkeypatch.setattr(resource_module, "get_catalog", lambda ref: catalog)
    monkeypatch.setattr(resource_module, "get_table_stats", get_stats)

    snapshot_id, result = resource._compaction_metadata("raw.events", "main")

    assert snapshot_id == 41
    assert result == stats
    catalog.load_table.assert_called_once_with("raw.events")
    get_stats.assert_called_once_with(table_name="raw.events", ref="main", table=table)


def test_execute_requires_executor_and_rejects_unsafe_identifier(monkeypatch) -> None:
    resource = _resource(
        monkeypatch,
        [
            (41, {"file_count": 1, "snapshot_count": 1}),
            (41, {"file_count": 1, "snapshot_count": 1}),
        ],
    )

    result = resource.compact(table_name="raw.events", expected_snapshot_id=41)
    assert result["failure"]["code"] == "maintenance_executor_required"
    assert result["retry_safe"] is True

    with pytest.raises(ValueError, match="namespace.table"):
        resource.compact(table_name="raw.events; DROP TABLE other", dry_run=True)
