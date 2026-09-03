"""Tests for the durable operation journal (ADR 0049 §1, Plan 010 Step 1)."""

from __future__ import annotations

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


def test_claim_succeeds_and_state_is_claimed(store) -> None:
    entry = _claim(store)
    assert entry.state is OperationJournalState.CLAIMED
    assert entry.operation_id == "op:1"


def test_conflicting_claim_is_rejected(store) -> None:
    _claim(store)
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _claim(store, subject="other-operator")


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
    _claim(store)
    mark_submitted(store, "op:1")
    mark_unknown(store, "op:1")
    reconcile_unknown(store, "op:1", {"accepted": True})
    assert store.read("op:1").state is OperationJournalState.SUCCEEDED


def test_read_or_replay_returns_none_for_new_operation(store) -> None:
    assert read_or_replay(store, "nonexistent") is None


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
