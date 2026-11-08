"""Tests for the durable operation journal (ADR 0049 §1, Plan 010 Step 1)."""

from __future__ import annotations

import json
from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest

from phlo.operations.journal import (
    InMemoryOperationJournalStore,
    OperationJournalError,
    OperationJournalState,
    claim_operation,
    complete_operation,
    mark_submitted,
    mark_unknown,
    read_or_replay,
    reconcile_unknown,
)


@pytest.fixture()
def store():
    return InMemoryOperationJournalStore()


def _claim(
    store, *, operation_id="op:1", subject="operator", action="compact", target="t", token="tok"
):
    return claim_operation(
        store,
        operation_id=operation_id,
        subject=subject,
        action=action,
        target=target,
        plan_token=token,
    )


def _concurrent_file_claim(directory: str, operation_id: str, start: Event, results: Queue) -> None:
    """Claim one target from a separate process after the shared start signal."""
    from phlo.operations.journal_store import FileOperationJournalStore

    start.wait(timeout=5)
    store = FileOperationJournalStore(directory)
    try:
        entry = _claim(
            store,
            operation_id=operation_id,
            action="restore.apply",
            target="/srv/same-target",
            token=operation_id,
        )
    except OperationJournalError:
        results.put(False)
    else:
        results.put(entry.operation_id == operation_id)


def test_claim_succeeds_and_state_is_claimed(store) -> None:
    entry = _claim(store)
    assert entry.state is OperationJournalState.CLAIMED
    assert entry.operation_id == "op:1"


@pytest.mark.parametrize(
    "first_action, second_action, second_id",
    [
        ("compact", "compact", "op:1"),
        ("expire_snapshots", "snapshot_expiry", "fresh-plan"),
        ("snapshot_expiry", "expire_snapshots", "fresh-plan"),
    ],
)
def test_conflicting_claim_is_rejected(store, first_action, second_action, second_id) -> None:
    _claim(store, action=first_action)
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _claim(store, subject="other-operator", action=second_action, operation_id=second_id)


def test_mark_submitted_moves_to_submitted(store) -> None:
    _claim(store)
    mark_submitted(store, "op:1")
    assert store.read("op:1").state is OperationJournalState.SUBMITTED


def test_mark_submitted_without_claim_raises(store) -> None:
    with pytest.raises(OperationJournalError, match="unknown_operation"):
        mark_submitted(store, "nonexistent")


def test_complete_operation_records_succeeded(store) -> None:
    _claim(store)
    mark_submitted(store, "op:1")
    complete_operation(store, "op:1", {"accepted": True, "status": "succeeded"})
    entry = store.read("op:1")
    assert entry.state is OperationJournalState.SUCCEEDED
    assert entry.result["accepted"] is True


def test_complete_operation_records_failed(store) -> None:
    _claim(store)
    mark_submitted(store, "op:1")
    complete_operation(store, "op:1", {"accepted": False, "status": "failed"})
    assert store.read("op:1").state is OperationJournalState.FAILED


def test_mark_unknown_blocks_replay(store) -> None:
    _claim(store)
    mark_submitted(store, "op:1")
    mark_unknown(store, "op:1")
    assert store.read("op:1").state is OperationJournalState.UNKNOWN
    with pytest.raises(OperationJournalError, match="unknown_outcome_blocks_replay"):
        read_or_replay(store, "op:1")


def test_reconcile_unknown_resolves_to_succeeded(store) -> None:
    _claim(store, action="backup.create")
    mark_submitted(store, "op:1")
    mark_unknown(store, "op:1")
    reconcile_unknown(store, "op:1", {"accepted": True})
    assert store.read("op:1").state is OperationJournalState.SUCCEEDED


@pytest.mark.parametrize("operation", ["compact", "snapshot_expiry", "expire_snapshots"])
@pytest.mark.parametrize(
    "observed",
    [
        {"accepted": True, "status": "noop"},
        {"accepted": True, "status": "succeeded"},
        {"accepted": False, "status": "failed", "failure": {"outcome": "not_submitted"}},
    ],
)
def test_maintenance_reconciliation_requires_definitive_evidence(tmp_path, operation, observed):
    from phlo.operations.journal_store import FileOperationJournalStore

    directory = tmp_path / "journal"
    store = FileOperationJournalStore(directory)
    _claim(store, action=operation)
    # Preserve the historical action as persisted by older scheduled writers.
    path = next(directory.glob("*.json"))
    record = json.loads(path.read_text())
    record["action"] = operation
    path.write_text(json.dumps(record))
    mark_submitted(store, "op:1")
    mark_unknown(store, "op:1")
    ambiguous = {
        "accepted": True,
        "status": "failed",
        "executed": True,
        "retry_safe": False,
        "failure": {"outcome": "unknown"},
    }
    for evidence in (ambiguous, {"accepted": True}, {"accepted": False}):
        with pytest.raises(OperationJournalError, match="unknown_outcome_blocks_replay"):
            reconcile_unknown(store, "op:1", evidence)
        reopened = FileOperationJournalStore(directory)
        assert reopened.read("op:1").state is OperationJournalState.UNKNOWN
        with pytest.raises(OperationJournalError, match="unknown_outcome_blocks_replay"):
            read_or_replay(reopened, "op:1")
        with pytest.raises(OperationJournalError, match="conflicting_claim"):
            _claim(reopened, operation_id="op:fresh", action=operation, token="fresh")

    reconcile_unknown(store, "op:1", observed)
    reopened = FileOperationJournalStore(directory)
    assert reopened.read("op:1").state == ("succeeded" if observed["accepted"] else "failed")
    assert read_or_replay(reopened, "op:1") == observed
    _claim(reopened, operation_id="op:fresh", action=operation, token="fresh")


def test_read_or_replay_returns_none_for_new_operation(store) -> None:
    assert read_or_replay(store, "nonexistent") is None


@pytest.mark.parametrize("persisted_action", ["expire_snapshots", "snapshot_expiry"])
@pytest.mark.parametrize("state", ["claimed", "submitted", "unknown"])
def test_persisted_snapshot_expiry_alias_fences_fresh_plans(tmp_path, persisted_action, state):
    from phlo.operations.journal import OperationJournalEntry
    from phlo.operations.journal_store import FileOperationJournalStore

    directory = tmp_path / "journal"
    store = FileOperationJournalStore(directory)
    # Write the on-disk format from an older writer, without normalising its action.
    record = OperationJournalEntry(
        "legacy-retention",
        "scheduler",
        persisted_action,
        "raw.events",
        "old-plan",
        OperationJournalState(state),
        result={"accepted": True},
    ).to_dict()
    path = directory / "legacy-retention.json"
    path.write_text(json.dumps(record))
    original = path.read_bytes()
    other_action = (
        "snapshot_expiry" if persisted_action == "expire_snapshots" else "expire_snapshots"
    )
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _claim(store, operation_id="fresh", action=other_action, target="raw.events", token="new")
    assert path.read_bytes() == original
    # Different targets and unrelated actions do not become equivalent.
    _claim(store, operation_id="other-table", action=other_action, target="raw.other")
    _claim(store, operation_id="compact", action="compact", target="raw.events")
    store.transition("legacy-retention", OperationJournalState.UNKNOWN, record["result"])
    reconcile_unknown(store, "legacy-retention", {"accepted": True, "status": "succeeded"})
    # Direct store claims must normalise new rows too, not only claim_operation.
    assert store.claim(
        OperationJournalEntry(
            "fresh",
            "cli",
            other_action,
            "raw.events",
            "new",
            OperationJournalState.CLAIMED,
        )
    )
    assert store.read("fresh").action == "snapshot_expiry"
    assert store.read("legacy-retention").action == persisted_action


def test_read_or_replay_returns_stored_result_for_succeeded(store) -> None:
    _claim(store)
    mark_submitted(store, "op:1")
    complete_operation(store, "op:1", {"accepted": True, "status": "succeeded"})
    result = read_or_replay(store, "op:1")
    assert result is not None
    assert result["accepted"] is True


# --- durable (cross-process) store ---------------------------------------


def test_file_journal_survives_across_store_instances(tmp_path) -> None:
    from phlo.operations.journal_store import FileOperationJournalStore

    first = FileOperationJournalStore(tmp_path / "journal")
    _claim(
        first,
        operation_id="op:durable",
        action="restore.apply",
        target="/srv/target",
        token="tok-1",
    )
    mark_submitted(first, "op:durable")
    complete_operation(first, "op:durable", {"accepted": True})

    # A brand-new instance (simulating a fresh process) observes the same state.
    second = FileOperationJournalStore(tmp_path / "journal")
    entry = second.read("op:durable")
    assert entry is not None
    assert entry.state is OperationJournalState.SUCCEEDED
    assert entry.result == {"accepted": True}
    entry = _claim(
        second, operation_id="op:other", action="restore.apply", target="/srv/target", token="tok-2"
    )
    assert entry.state is OperationJournalState.CLAIMED


def test_file_journal_rejects_active_conflict_across_processes(tmp_path) -> None:
    from phlo.operations.journal_store import FileOperationJournalStore

    first = FileOperationJournalStore(tmp_path / "journal")
    _claim(
        first,
        operation_id="op:a",
        action="upgrade.apply",
        target="deploy-1",
        token="tok",
    )
    second = FileOperationJournalStore(tmp_path / "journal")
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _claim(
            second,
            operation_id="op:b",
            action="upgrade.apply",
            target="deploy-1",
            token="tok",
        )


def test_file_journal_serializes_concurrent_cross_process_claims(tmp_path, monkeypatch) -> None:
    """Only one simultaneously-started process can claim a shared target."""
    # Spawned children (the macOS/Windows default) re-import this module as
    # ``tests.operations.test_operation_journal`` in a fresh interpreter whose
    # ``sys.path`` lacks the repo root. Prepend it: spawn propagates the
    # parent's ``sys.path`` to children, while fork-based platforms resolve
    # the module already and ignore this.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    start = Event()
    results: Queue = Queue()
    processes = [
        Process(
            target=_concurrent_file_claim,
            args=(str(tmp_path / "journal"), f"op:{index}", start, results),
        )
        for index in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sum(results.get(timeout=2) for _ in processes) == 1


def test_file_journal_allows_new_claim_after_terminal_state(tmp_path) -> None:
    from phlo.operations.journal_store import FileOperationJournalStore

    store = FileOperationJournalStore(tmp_path / "journal")
    _claim(store, operation_id="op:1", action="backup.create", target="/srv/backup", token="s1")
    complete_operation(store, "op:1", {"accepted": True})
    # A later, unrelated operation over the same (action, target) is permitted
    # once the earlier claim is terminal.
    entry = _claim(
        store, operation_id="op:2", action="backup.create", target="/srv/backup", token="s2"
    )
    assert entry.state is OperationJournalState.CLAIMED


def test_file_journal_safe_name_does_not_collide_on_sanitized_refs(tmp_path) -> None:
    """Refs differing only in collapsed separators must map to distinct files."""
    from phlo.operations.journal_store import FileOperationJournalStore

    slash = FileOperationJournalStore._safe_name("expire:t:feature/orders:run-1")
    underscore = FileOperationJournalStore._safe_name("expire:t:feature_orders:run-1")
    assert slash != underscore

    # Operation ids already filesystem-safe keep their exact names unchanged.
    assert FileOperationJournalStore._safe_name("op-clean.name_1") == "op-clean.name_1"

    store = FileOperationJournalStore(tmp_path / "journal")
    _claim(
        store,
        operation_id="expire:t:feature/orders:run-1",
        action="expire_snapshots",
        target="t",
        token="p1",
    )
    complete_operation(store, "expire:t:feature/orders:run-1", {"accepted": True})
    entry = _claim(
        store,
        operation_id="expire:t:feature_orders:run-1",
        action="expire_snapshots",
        target="t",
        token="p2",
    )
    assert entry.operation_id == "expire:t:feature_orders:run-1"
    read_back = store.read("expire:t:feature/orders:run-1")
    assert read_back is not None
    assert read_back.operation_id == "expire:t:feature/orders:run-1"
    assert read_back.state is OperationJournalState.SUCCEEDED
