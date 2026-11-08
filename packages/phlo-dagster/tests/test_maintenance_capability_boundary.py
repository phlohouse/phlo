"""Guard the provider-neutral boundary of Dagster maintenance flows.

Retention ops resolve only table_store and neutral executor capabilities --
never concrete providers like Trino -- against structural fakes of the
retention contract.
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from phlo_dagster import iceberg_maintenance
from phlo_dagster.iceberg_maintenance_utils import MaintenanceConfig


class FakeMaintenanceRetentionStore:
    """Structural fake for the provider-neutral Dagster retention contract."""

    def __init__(self) -> None:
        self.expected_snapshot_id: int | str | None = None
        self.executor: object | None = None

    def _plan(self) -> dict[str, object]:
        return {
            "operation": "cleanup_orphan_files",
            "table_name": "raw.events",
            "ref": "main",
            "catalog": "iceberg",
            "retention_hours": 168,
            "before_snapshot_id": 41,
            "candidate_snapshots": [{"snapshot_id": 40}],
            "candidate_files": [{"path": "orphan.parquet", "size_bytes": 100}],
            "protected_snapshot_ids": [41],
            "table_snapshot_refs": {},
            "table_snapshot_ref_evidence": "available",
            "nessie_ref_evidence": "unavailable",
            "scan_status": "available",
            "affected_objects": 1,
            "affected_bytes": 100,
            "unavailable_fields": [],
            "plan_token": "fake-plan-token",
        }

    def expire_snapshots(self, **kwargs: object) -> dict[str, object]:
        return self._result(kwargs, "expire_snapshots")

    def cleanup_orphan_files(self, **kwargs: object) -> dict[str, object]:
        return self._result(kwargs, "cleanup_orphan_files")

    def _result(self, kwargs: dict[str, object], operation: str) -> dict[str, object]:
        plan = self._plan()
        plan["operation"] = operation
        if kwargs.get("dry_run"):
            return {
                "status": "planned",
                "accepted": True,
                "executed": False,
                "before_revision": 41,
                "plan_token": "fake-plan-token",
                "planned": plan,
            }
        self.expected_snapshot_id = kwargs.get("expected_snapshot_id")
        self.executor = kwargs.get("executor")
        if operation == "expire_snapshots" and self.executor is not None:
            return {
                "status": "succeeded",
                "accepted": True,
                "executed": True,
                "before_revision": 41,
                "after_revision": 42,
                "plan_token": "fake-plan-token",
                "planned": plan,
                "affected": {"observed_snapshot_count_reduction": 1},
            }
        return {
            "status": "blocked",
            "accepted": False,
            "executed": False,
            "before_revision": 41,
            "plan_token": "fake-plan-token",
            "planned": {**plan, "trino_boundary": "not_invoked"},
            "failure": {
                "code": "bounded_execution_unsupported",
                "message": "The provider accepts only a threshold; no deletion is submitted.",
                "retryable": False,
            },
            "retry_safe": False,
        }


class FakeSnapshotExpiryExecutor:
    """Structural fake for the declared, neutral executor contract."""

    def for_ref(self, ref: str):
        assert ref == "main"
        return self

    def compact_table(self, **_: object) -> dict[str, object]:
        return {}

    def expire_snapshots_table(self, **_: object) -> dict[str, object]:
        return {}


def test_maintenance_source_does_not_import_concrete_provider_modules() -> None:
    """Retention orchestration may import Phlo core and itself, not provider packages."""
    source_root = Path(__file__).parents[1] / "src" / "phlo_dagster"
    provider_imports: list[ast.AST] = []
    maintenance_sources = (
        "iceberg_maintenance.py",
        "iceberg_maintenance_utils.py",
        "maintenance_policy.py",
        "maintenance_sensor.py",
    )
    for filename in maintenance_sources:
        source_path = source_root / filename
        tree = ast.parse(source_path.read_text())
        provider_imports.extend(
            node
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("phlo_")
                and not node.module.startswith("phlo_dagster")
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    alias.name.startswith("phlo_") and not alias.name.startswith("phlo_dagster")
                    for alias in node.names
                )
            )
        )

    assert provider_imports == []


def test_orphan_execute_uses_plan_result_and_refuses_without_executor(monkeypatch) -> None:
    """The Dagster op forwards the real plan fence and never resolves Trino."""
    store = FakeMaintenanceRetentionStore()
    resolutions: list[tuple[str, str]] = []

    def resolve(capability_type: str, name: str):
        resolutions.append((capability_type, name))
        return MagicMock(provider=store)

    monkeypatch.setattr(iceberg_maintenance, "resolve_capability", resolve)
    monkeypatch.setattr(iceberg_maintenance, "resolve_namespaces", lambda config: ["raw"])
    monkeypatch.setattr(iceberg_maintenance, "list_tables", lambda namespace, ref: ["raw.events"])
    monkeypatch.setattr(
        iceberg_maintenance,
        "finish_maintenance_op",
        MagicMock(return_value={"status": "success"}),
    )
    config = MaintenanceConfig(
        namespace="raw",
        ref="main",
        dry_run=False,
        catalog="iceberg",
        confirmation_token="fake-plan-token",
        max_affected_objects=10,
        max_affected_bytes=1000,
    )

    context = MagicMock()
    context.run_id = "run-1"
    context.job_name = "maintenance"
    result = iceberg_maintenance.cleanup_orphan_files.compute_fn.decorated_fn(context, config)

    assert resolutions == [("table_store", "iceberg")]
    assert store.expected_snapshot_id == 41
    assert result["tables_processed"] == 1
    assert result["total_candidate_files"] == 1
    assert result["total_deleted_files"] == 0
    assert result["errors"]
    warning_messages = [call.args[0] for call in context.log.warning.call_args_list]
    assert any("no provider deletion is submitted" in message for message in warning_messages)
    assert any("only a threshold" in message for message in warning_messages)


def test_snapshot_expiry_completion_telemetry_includes_candidate_count(monkeypatch) -> None:
    """Completion telemetry records every snapshot identified by planning."""
    store = FakeMaintenanceRetentionStore()
    finish = MagicMock(return_value={"status": "success"})
    monkeypatch.setattr(
        iceberg_maintenance,
        "resolve_capability",
        lambda capability_type, name: MagicMock(provider=store),
    )
    monkeypatch.setattr(iceberg_maintenance, "resolve_namespaces", lambda config: ["raw"])
    monkeypatch.setattr(
        iceberg_maintenance,
        "list_tables",
        lambda namespace, ref: ["raw.events"],
    )
    monkeypatch.setattr(
        iceberg_maintenance,
        "start_maintenance_op",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(iceberg_maintenance, "finish_maintenance_op", finish)
    context = MagicMock(run_id="run-1", job_name="maintenance")

    result = iceberg_maintenance.expire_table_snapshots.compute_fn.decorated_fn(
        context,
        MaintenanceConfig(namespace="raw", ref="main", dry_run=True, catalog="iceberg"),
    )

    assert finish.call_args.kwargs["total_candidate_snapshots"] == 1
    assert result["total_candidate_snapshots"] == 1


def test_snapshot_expiry_execute_resolves_and_passes_neutral_executor(monkeypatch) -> None:
    from phlo.operations.journal import InMemoryOperationJournalStore

    store = FakeMaintenanceRetentionStore()
    executor = FakeSnapshotExpiryExecutor()
    journal = InMemoryOperationJournalStore()
    resolutions: list[tuple[str, str | None]] = []

    def resolve(capability_type: str, name: str | None = None):
        resolutions.append((capability_type, name))
        return MagicMock(provider=store if capability_type == "table_store" else executor)

    monkeypatch.setattr(iceberg_maintenance, "resolve_capability", resolve)
    monkeypatch.setattr(iceberg_maintenance, "resolve_namespaces", lambda config: ["raw"])
    monkeypatch.setattr(iceberg_maintenance, "list_tables", lambda namespace, ref: ["raw.events"])
    monkeypatch.setattr(iceberg_maintenance, "durable_maintenance_journal", lambda: journal)
    context = MagicMock(run_id="run-1", job_name="maintenance")

    result = iceberg_maintenance.expire_table_snapshots.compute_fn.decorated_fn(
        context,
        MaintenanceConfig(
            namespace="raw",
            ref="main",
            dry_run=False,
            catalog="iceberg",
            confirmation_token="fake-plan-token",
        ),
    )

    assert resolutions == [("table_store", "iceberg"), ("maintenance_executor", None)]
    assert store.executor is executor
    assert result["total_snapshots_deleted"] == 0
    entry = journal.read("expire_snapshots:raw.events:main:run-1")
    assert entry is not None
    assert entry.state == "succeeded"
    assert entry.plan_token == "fake-plan-token"


def _wire_expire_op(
    monkeypatch, store: object, journal: object, executor: object | None = None
) -> MagicMock:
    """Patch capability resolution, table listing, journal, and telemetry."""
    monkeypatch.setattr(
        iceberg_maintenance,
        "resolve_capability",
        lambda capability_type, name=None: MagicMock(
            provider=store if capability_type == "table_store" else executor
        ),
    )
    monkeypatch.setattr(iceberg_maintenance, "resolve_namespaces", lambda config: ["raw"])
    monkeypatch.setattr(iceberg_maintenance, "list_tables", lambda namespace, ref: ["raw.events"])
    monkeypatch.setattr(iceberg_maintenance, "durable_maintenance_journal", lambda: journal)
    monkeypatch.setattr(
        iceberg_maintenance, "start_maintenance_op", MagicMock(return_value=MagicMock())
    )
    finish = MagicMock(return_value={"status": "success"})
    monkeypatch.setattr(iceberg_maintenance, "finish_maintenance_op", finish)
    return finish


@pytest.mark.parametrize(
    "failure, accepted, executed",
    [
        ({"code": "outcome_unknown_after_submission", "outcome": "unknown"}, True, True),
        ({"code": "provider_timeout"}, True, True),
        ({"code": "outcome_unknown_after_submission"}, False, False),
    ],
)
def test_snapshot_expiry_journals_outcome_unknown(
    monkeypatch, tmp_path, failure, accepted, executed
) -> None:
    """A submitted-but-unconfirmed provider result records UNKNOWN, not FAILED."""
    from phlo.operations.journal import (
        OperationJournal,
        OperationJournalError,
        OperationJournalState,
    )
    from phlo.operations.journal_store import FileOperationJournalStore

    plan_result = {
        "status": "planned",
        "accepted": True,
        "executed": False,
        "before_revision": 41,
        "plan_token": "fake-plan-token",
        "planned": {"candidate_snapshots": [{"snapshot_id": 40}]},
    }
    unknown_result = {
        "status": "failed",
        "accepted": accepted,
        "executed": executed,
        "before_revision": 41,
        "plan_token": "fake-plan-token",
        "failure": failure,
        "retry_safe": False,
    }
    store = FakeMaintenanceRetentionStore()
    store.expire_snapshots = MagicMock(side_effect=[plan_result, unknown_result])
    directory = tmp_path / "journal"
    journal = FileOperationJournalStore(directory)
    _wire_expire_op(monkeypatch, store, journal, executor=FakeSnapshotExpiryExecutor())
    context = MagicMock(run_id="run-1", job_name="maintenance")

    iceberg_maintenance.expire_table_snapshots.compute_fn.decorated_fn(
        context,
        MaintenanceConfig(
            namespace="raw",
            ref="main",
            dry_run=False,
            catalog="iceberg",
            confirmation_token="fake-plan-token",
        ),
    )

    journal = FileOperationJournalStore(directory)
    operation_id = "expire_snapshots:raw.events:main:run-1"
    entry = journal.read(operation_id)
    assert entry is not None
    # UNKNOWN is an active claim: a later run cannot re-claim or replay it.
    assert entry.state == "unknown"
    assert entry.result == unknown_result
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        OperationJournal(journal).claim(
            operation_id="snapshot_expiry:raw.events:main:fresh-plan",
            subject="cli",
            action="snapshot_expiry",
            target="raw.events",
            plan_token="fresh-plan",
        )
    with pytest.raises(OperationJournalError, match="unknown_outcome_blocks_replay"):
        OperationJournal(journal).reconcile(
            operation_id, state=OperationJournalState.SUCCEEDED, result={"accepted": True}
        )
    assert FileOperationJournalStore(directory).read(operation_id) == entry


def test_snapshot_expiry_exception_retains_cause_and_unknown_fence(monkeypatch, tmp_path, capfd):
    from phlo.operations.journal_store import FileOperationJournalStore

    class DisconnectingStore(FakeMaintenanceRetentionStore):
        def __init__(self):
            super().__init__()
            self.submissions = []

        def expire_snapshots(self, **kwargs):
            if kwargs.get("dry_run"):
                return super().expire_snapshots(**kwargs)
            self.submissions.append(kwargs["operation_id"])
            raise ConnectionError("connection reset after submission")

    store = DisconnectingStore()
    executor = FakeSnapshotExpiryExecutor()
    monkeypatch.setattr(
        iceberg_maintenance,
        "resolve_capability",
        lambda kind, name=None: MagicMock(provider=store if kind == "table_store" else executor),
    )
    monkeypatch.setattr(iceberg_maintenance, "resolve_namespaces", lambda config: ["raw"])
    monkeypatch.setattr(iceberg_maintenance, "list_tables", lambda namespace, ref: ["raw.events"])
    directory = tmp_path / "journal"
    monkeypatch.setenv("PHLO_OPERATIONS_JOURNAL_DIR", str(directory))
    monkeypatch.setenv("PHLO_TELEMETRY_PATH", str(tmp_path / "telemetry.jsonl"))
    config = {
        "ops": {
            "expire_table_snapshots": {
                "config": {
                    "namespace": "raw",
                    "dry_run": False,
                    "confirmation_token": "fake-plan-token",
                }
            }
        }
    }

    first = iceberg_maintenance.expire_snapshots_job.execute_in_process(run_config=config)
    fresh = iceberg_maintenance.expire_snapshots_job.execute_in_process(run_config=config)
    assert first.success and fresh.success
    operation_id = f"expire_snapshots:raw.events:main:{first.run_id}"
    entry = FileOperationJournalStore(directory).read(operation_id)
    assert entry is not None and entry.state == "unknown"
    assert entry.result is None  # No provider result or certainty was invented.
    assert "conflicting_claim" in " ".join(
        fresh.output_for_node("expire_table_snapshots")["errors"]
    )
    assert store.submissions == [operation_id]
    assert len(list(directory.glob("*.json"))) == 1

    errors = first.output_for_node("expire_table_snapshots")["errors"]
    assert len(errors) == 1
    for detail in (
        "apply_outcome_unknown",
        operation_id,
        "ConnectionError",
        "connection reset after submission",
    ):
        assert detail in errors[0]
    assert errors[0] in capfd.readouterr().err


@pytest.mark.parametrize("confirmation_token", ["fake-plan-token", None, "other-token"])
def test_snapshot_expiry_replays_journaled_result_without_provider_access(
    monkeypatch, confirmation_token
) -> None:
    """A stored result replays even when planning and executor resolution fail."""
    from phlo.operations.journal import (
        InMemoryOperationJournalStore,
        OperationJournal,
        OperationJournalState,
    )

    stored = {
        "status": "succeeded",
        "accepted": True,
        "executed": True,
        "planned": {"candidate_snapshots": [{"snapshot_id": 40}]},
        "affected": {"observed_snapshot_count_reduction": 1},
    }
    journal = InMemoryOperationJournalStore()
    operation_id = "expire_snapshots:raw.events:main:run-1"
    owner = OperationJournal(journal)
    owner.claim(
        operation_id=operation_id,
        subject="dagster:maintenance-policy",
        action="expire_snapshots",
        target="raw.events",
        plan_token="fake-plan-token",
    )
    owner.submit(operation_id)
    owner.complete(operation_id, state=OperationJournalState.SUCCEEDED, result=stored)

    # Any provider access (planning dry-run or executor resolution) must not be
    # reached: the journaled result replays first.
    store = FakeMaintenanceRetentionStore()
    store.expire_snapshots = MagicMock(side_effect=RuntimeError("catalog unavailable"))
    _wire_expire_op(monkeypatch, store, journal, executor=None)
    monkeypatch.setattr(
        iceberg_maintenance,
        "_load_snapshot_expiry_executor",
        MagicMock(side_effect=AssertionError("executor resolved before replay")),
    )
    context = MagicMock(run_id="run-1", job_name="maintenance")

    result = iceberg_maintenance.expire_table_snapshots.compute_fn.decorated_fn(
        context,
        MaintenanceConfig(
            namespace="raw",
            ref="main",
            dry_run=False,
            catalog="iceberg",
            confirmation_token=confirmation_token,
        ),
    )

    store.expire_snapshots.assert_not_called()
    assert result["tables_processed"] == 1
