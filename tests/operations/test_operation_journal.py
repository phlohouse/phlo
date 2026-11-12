"""Journal contracts shared by the memory and cross-process file adapters."""

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Event, Process, Queue
from pathlib import Path

import pytest

from phlo.operations.journal import (
    InMemoryOperationJournalStore,
    OperationJournal,
    OperationJournalError,
    execute_maintenance,
)
from phlo.operations.journal import (
    OperationJournalState as State,
)
from phlo.operations.journal_store import FileOperationJournalStore


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryOperationJournalStore()
    return FileOperationJournalStore(tmp_path / "journal")


def _claim(store, *, operation_id="op:1", action="compact", target="t", **kwargs):
    return OperationJournal(store).claim(
        operation_id=operation_id,
        subject="operator",
        action=action,
        target=target,
        plan_token="tok",
        **kwargs,
    )


def _replay(store, operation_id="op:1", **kwargs):
    binding = {"subject": "operator", "action": "compact", "target": "t", "plan_token": "tok"}
    binding.update(kwargs)
    return OperationJournal(store).replay(operation_id, **binding)


def _advance(store, state):
    owner = OperationJournal(store)
    if state is State.CLAIMED:
        return
    owner.submit("op:1")
    if state is State.UNKNOWN:
        owner.unknown("op:1", result={"query_id": "q1"})
    elif state in {State.SUCCEEDED, State.FAILED}:
        owner.complete("op:1", state=state, result={"receipt": "q1", "state": state.value})


@pytest.mark.parametrize("before", list(State))
@pytest.mark.parametrize("after", list(State))
def test_adapter_enforces_complete_transition_graph(store, before, after):
    """A direct persistence caller cannot bypass the core transition graph."""
    _claim(store)
    _advance(store, before)
    original = store.read("op:1")
    legal = {
        State.CLAIMED: {State.SUBMITTED, State.FAILED},
        State.SUBMITTED: {State.SUCCEEDED, State.FAILED, State.UNKNOWN},
        State.UNKNOWN: {State.SUCCEEDED, State.FAILED},
    }
    if after in legal.get(before, set()):
        assert store.compare_and_set("op:1", before, after, {"receipt": "q2"})
        assert store.read("op:1").state is after
        assert store.read("op:1").result == {"receipt": "q2"}
    else:
        with pytest.raises(OperationJournalError, match="invalid_transition"):
            store.compare_and_set("op:1", before, after, {"receipt": "q2"})
        assert store.read("op:1") == original


@pytest.mark.parametrize("state", list(State))
def test_claim_fence_and_replay_have_the_same_terminal_boundary(store, state):
    _claim(store)
    _advance(store, state)
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _claim(store)
    if state in {State.SUCCEEDED, State.FAILED}:
        assert _replay(store) == {"receipt": "q1", "state": state.value}
        assert _claim(store, operation_id="fresh").state is State.CLAIMED
    else:
        with pytest.raises(OperationJournalError, match="conflicting_claim"):
            _claim(store, operation_id="fresh")
        with pytest.raises(OperationJournalError, match="operation_in_progress|unknown_outcome"):
            _replay(store)


@pytest.mark.parametrize("state", [State.SUCCEEDED, State.FAILED])
def test_terminal_receipt_cannot_be_erased_or_reconciled_again(store, state):
    _claim(store)
    _advance(store, state)
    original = store.read("op:1")
    owner = OperationJournal(store)
    for command in (
        lambda: owner.submit("op:1"),
        lambda: owner.unknown("op:1"),
        lambda: owner.complete("op:1", state=state, result={"changed": True}),
        lambda: owner.reconcile(
            "op:1", state=State.FAILED, result={"accepted": False, "status": "failed"}
        ),
    ):
        with pytest.raises(OperationJournalError, match="invalid_transition"):
            command()
        assert store.read("op:1") == original


@pytest.mark.parametrize(
    "binding",
    [
        {"subject": "other"},
        {"action": "restore.apply"},
        {"target": "other"},
        {"plan_token": "other"},
    ],
)
def test_replay_rejects_changed_binding(store, binding):
    _claim(store)
    _advance(store, State.SUCCEEDED)
    with pytest.raises(OperationJournalError, match="binding_mismatch"):
        _replay(store, **binding)


def test_missing_operation_and_pre_submission_rejection(store):
    owner = OperationJournal(store)
    assert _replay(store) is None
    with pytest.raises(OperationJournalError, match="unknown_operation"):
        owner.submit("op:1")
    _claim(store)
    owner.reject("op:1", result={"accepted": False, "reason": "preflight"})
    assert store.read("op:1").state is State.FAILED
    assert _replay(store)["reason"] == "preflight"


def test_unknown_requires_reconciliation_not_ordinary_completion(store):
    _claim(store, action="restore.apply")
    _advance(store, State.UNKNOWN)
    owner = OperationJournal(store)
    with pytest.raises(OperationJournalError, match="invalid_transition"):
        owner.complete("op:1", state=State.SUCCEEDED, result={"accepted": True})
    assert store.read("op:1").result == {"query_id": "q1"}
    owner.reconcile("op:1", state=State.SUCCEEDED, result={"accepted": True})
    assert _replay(store, action="restore.apply") == {"accepted": True}


@pytest.mark.parametrize(
    "action,alias",
    [
        ("compact", "compact"),
        ("backup.create", "backup.create"),
        ("restore.apply", "restore.apply"),
        ("expire_snapshots", "snapshot_expiry"),
        ("snapshot_expiry", "expire_snapshots"),
    ],
)
def test_canonical_action_target_is_the_exclusion_identity(store, action, alias):
    _claim(store, action=action)
    with pytest.raises(OperationJournalError, match="conflicting_claim"):
        _claim(store, operation_id="other", action=alias)
    _claim(store, operation_id="different-target", action=alias, target="other")
    _claim(store, operation_id="different-action", action="unrelated")


@pytest.mark.parametrize("state", [State.CLAIMED, State.SUBMITTED, State.UNKNOWN])
def test_only_expired_pre_submission_claims_can_be_taken_over(store, state):
    _claim(store, claim_expiry="2999-01-01T00:00:00+00:00")
    _advance(store, state)
    # Clock crossing the lease boundary must not expire already submitted work.
    from datetime import UTC, datetime
    from unittest.mock import patch

    import phlo.operations.journal as journal_module

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(3000, 1, 1, tzinfo=UTC)

    with patch.object(journal_module, "datetime", FutureClock):
        if state is State.CLAIMED:
            with pytest.raises(OperationJournalError, match="claim_expired"):
                OperationJournal(store).submit("op:1")
            _claim(store, operation_id="replacement")
            assert store.read("op:1").state is State.FAILED
            assert store.read("op:1").result["failure"]["reason"] == "claim_expired"
            with pytest.raises(OperationJournalError, match="claim_expired"):
                _replay(store)
            with pytest.raises(OperationJournalError, match="invalid_transition"):
                OperationJournal(store).submit("op:1")
        else:
            with pytest.raises(OperationJournalError, match="conflicting_claim"):
                _claim(store, operation_id="replacement")
            assert store.read("op:1").state is state


def test_atomic_expected_state_allows_only_one_completion(store):
    _claim(store, action="restore.apply")
    OperationJournal(store).submit("op:1")

    def finish(state):
        try:
            OperationJournal(store).complete("op:1", state=state, result={"winner": state.value})
        except OperationJournalError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(finish, [State.SUCCEEDED, State.FAILED])) == 1
    receipt = store.read("op:1")
    assert receipt.result == {"winner": receipt.state.value}


def test_evidence_is_detached_from_callers(store):
    _claim(store)
    OperationJournal(store).submit("op:1")
    evidence = {"accepted": True, "status": "succeeded", "rows": [7]}
    OperationJournal(store).complete("op:1", state=State.SUCCEEDED, result=evidence)
    evidence["rows"].append(8)
    store.read("op:1").result["rows"].append(9)
    _replay(store)["rows"].append(10)
    assert _replay(store)["rows"] == [7]


@pytest.mark.parametrize(
    "evidence",
    [
        {"accepted": False, "state": "succeeded"},
        {"accepted": True, "state": "failed"},
    ],
)
def test_explicit_outcome_cannot_disagree_with_its_evidence(store, evidence):
    _claim(store)
    OperationJournal(store).submit("op:1")
    with pytest.raises(OperationJournalError, match="inconsistent_outcome"):
        OperationJournal(store).complete("op:1", state=State.SUCCEEDED, result=evidence)
    assert store.read("op:1").state is State.SUBMITTED
    assert store.read("op:1").result is None


def test_maintenance_custom_serialization_retains_unclassified_evidence(store):
    class ProviderResult:
        def to_dict(self):
            return {"accepted": True, "status": "completed", "query_id": "q7"}

    entry = execute_maintenance(
        store,
        operation="compact",
        table="t",
        ref="main",
        plan_token="tok",
        subject="operator",
        execute=lambda _: ProviderResult(),
    )
    assert entry.state is State.UNKNOWN
    assert store.read(entry.operation_id).result == {
        "accepted": True,
        "status": "completed",
        "query_id": "q7",
    }


@pytest.mark.parametrize(
    "observed,state",
    [
        ({"accepted": True, "status": "succeeded"}, State.SUCCEEDED),
        ({"accepted": True, "status": "noop"}, State.SUCCEEDED),
        ({"accepted": False, "status": "failed", "executed": False}, State.FAILED),
        (
            {"accepted": True, "status": "failed", "executed": True, "retry_safe": False},
            State.UNKNOWN,
        ),
        ({"accepted": True}, State.UNKNOWN),
    ],
)
def test_maintenance_classifies_once_and_preserves_evidence(store, observed, state):
    entry = execute_maintenance(
        store,
        operation="compact",
        table="t",
        ref="main",
        plan_token="tok",
        subject="operator",
        execute=lambda _: observed,
    )
    assert entry.state is state
    assert store.read(entry.operation_id).result == observed
    if state is State.UNKNOWN:
        with pytest.raises(OperationJournalError, match="unknown_outcome_blocks_replay"):
            OperationJournal(store).reconcile(
                entry.operation_id, state=State.SUCCEEDED, result={"accepted": True}
            )
        OperationJournal(store).reconcile(
            entry.operation_id,
            state=State.SUCCEEDED,
            result={"accepted": True, "status": "succeeded"},
        )
        assert store.read(entry.operation_id).state is State.SUCCEEDED


def _concurrent_file_claim(directory, operation_id, start, results):
    start.wait(timeout=5)
    try:
        _claim(FileOperationJournalStore(directory), operation_id=operation_id)
    except OperationJournalError:
        results.put(False)
    else:
        results.put(True)


def test_file_journal_serializes_cross_process_claims(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    start, results = Event(), Queue()
    processes = [
        Process(target=_concurrent_file_claim, args=(str(tmp_path), f"op:{i}", start, results))
        for i in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert sum(results.get(timeout=2) for _ in processes) == 1


def test_file_receipt_survives_reopening_and_safe_names_do_not_collide(tmp_path):
    first = FileOperationJournalStore(tmp_path)
    _claim(first, operation_id="expire:t:feature/orders:run-1")
    owner = OperationJournal(first)
    owner.submit("expire:t:feature/orders:run-1")
    owner.complete(
        "expire:t:feature/orders:run-1", state=State.SUCCEEDED, result={"accepted": True}
    )
    second = FileOperationJournalStore(tmp_path)
    assert _replay(second, "expire:t:feature/orders:run-1") == {"accepted": True}
    _claim(second, operation_id="expire:t:feature_orders:run-1")
    assert second.read("expire:t:feature/orders:run-1").state is State.SUCCEEDED
    assert second.read("expire:t:feature_orders:run-1").state is State.CLAIMED
