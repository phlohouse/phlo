"""Durable operation journal (ADR 0049 §1).

Provider-neutral claim/binding/state/result types and the atomic
cross-process state machine. Core owns the state transitions; production
persistence is a provider-owned adapter (PostgreSQL), never core SQL.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from phlo.capabilities.maintenance import MaintenanceOperationResult


class OperationJournalState(StrEnum):
    """Atomic journal states (ADR 0049 §1)."""

    CLAIMED = "claimed"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class OperationJournalError(RuntimeError):
    """A journal operation failed a binding, expiry, or conflict check."""

    def __init__(self, code: str, identifiers: tuple[str, ...] = ()) -> None:
        self.code = code
        self.identifiers = identifiers
        super().__init__(f"{code}: {', '.join(identifiers)}")


@dataclass(frozen=True, slots=True)
class OperationJournalEntry:
    """One journal record with full binding and result evidence."""

    operation_id: str
    subject: str
    action: str
    target: str
    plan_token: str
    state: OperationJournalState
    claim_expiry: str = ""
    result: dict[str, Any] | None = None
    observation_time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "subject": self.subject,
            "action": self.action,
            "target": self.target,
            "plan_token": self.plan_token,
            "state": self.state.value,
            "claim_expiry": self.claim_expiry,
            "result": self.result,
            "observation_time": self.observation_time,
        }


class OperationJournalStore(Protocol):
    """Atomic persistence for the core-owned journal state machine."""

    def claim(self, entry: OperationJournalEntry) -> bool:
        """Atomically insert; return False if an unexpired conflicting claim exists."""
        ...

    def compare_and_set(
        self,
        operation_id: str,
        expected: OperationJournalState,
        state: OperationJournalState,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """Apply ``transition_entry`` atomically; False means missing/stale state."""
        ...

    def read(self, operation_id: str) -> OperationJournalEntry | None:
        """Read one entry by operation_id."""
        ...


class InMemoryOperationJournalStore:
    """Test/development-only in-memory journal store."""

    def __init__(self) -> None:
        self._entries: dict[str, OperationJournalEntry] = {}
        self._lock = RLock()

    def claim(self, entry: OperationJournalEntry) -> bool:
        entry = new_claim(entry)
        with self._lock:
            if entry.operation_id in self._entries:
                return False
            for operation_id, record in self._entries.items():
                record = expire_claim(record)
                self._entries[operation_id] = record
                if claims_conflict(record, entry):
                    return False
            self._entries[entry.operation_id] = deepcopy(entry)
            return True

    def compare_and_set(
        self,
        operation_id: str,
        expected: OperationJournalState,
        state: OperationJournalState,
        result: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            entry = self._entries.get(operation_id)
            if entry is None or entry.state is not expected:
                return False
            self._entries[operation_id] = transition_entry(entry, state, result)
            return True

    def read(self, operation_id: str) -> OperationJournalEntry | None:
        with self._lock:
            return deepcopy(self._entries.get(operation_id))


def new_claim(entry: OperationJournalEntry) -> OperationJournalEntry:
    """Validate the only insertion state, including direct adapter callers."""
    if entry.state is not OperationJournalState.CLAIMED or entry.result is not None:
        raise OperationJournalError("invalid_claim", (entry.operation_id,))
    expiry = entry.claim_expiry or (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    try:
        timestamp = datetime.fromisoformat(expiry)
    except ValueError as exc:
        raise OperationJournalError("invalid_claim_expiry", (entry.operation_id,)) from exc
    if timestamp.tzinfo is None:
        raise OperationJournalError("invalid_claim_expiry", (entry.operation_id,))
    return replace(entry, action=canonical_maintenance_action(entry.action), claim_expiry=expiry)


def claims_conflict(record: OperationJournalEntry, entry: OperationJournalEntry) -> bool:
    """One canonical action/target is fenced until its outcome is definitive."""
    return (
        canonical_maintenance_action(record.action) == entry.action
        and record.target == entry.target
        and record.state
        in {
            OperationJournalState.CLAIMED,
            OperationJournalState.SUBMITTED,
            OperationJournalState.UNKNOWN,
        }
    )


def expire_claim(entry: OperationJournalEntry) -> OperationJournalEntry:
    """Release only an expired pre-submission lease, retaining its receipt."""
    if entry.state is not OperationJournalState.CLAIMED or not entry.claim_expiry:
        return entry
    expiry = datetime.fromisoformat(entry.claim_expiry)
    if expiry.tzinfo is None or datetime.now(UTC) >= expiry:
        return replace(
            entry,
            state=OperationJournalState.FAILED,
            result={"accepted": False, "state": "failed", "failure": {"reason": "claim_expired"}},
        )
    return entry


def transition_entry(
    entry: OperationJournalEntry,
    state: OperationJournalState,
    result: dict[str, Any] | None,
) -> OperationJournalEntry:
    """The complete graph, evaluated while the adapter holds its write lock.

    Terminal records have no outgoing edges. UNKNOWN is resolved only by the
    owner's reconciliation command, never by completing a submitted operation.
    """
    edges = {
        OperationJournalState.CLAIMED: {
            OperationJournalState.SUBMITTED,
            OperationJournalState.FAILED,
        },
        OperationJournalState.SUBMITTED: {
            OperationJournalState.SUCCEEDED,
            OperationJournalState.FAILED,
            OperationJournalState.UNKNOWN,
        },
        OperationJournalState.UNKNOWN: {
            OperationJournalState.SUCCEEDED,
            OperationJournalState.FAILED,
        },
    }
    if state not in edges.get(entry.state, set()):
        raise OperationJournalError("invalid_transition", (entry.operation_id, entry.state, state))
    if state is OperationJournalState.SUBMITTED and expire_claim(entry) != entry:
        raise OperationJournalError("claim_expired", (entry.operation_id,))
    if state in {OperationJournalState.SUCCEEDED, OperationJournalState.FAILED} and result is None:
        raise OperationJournalError("missing_result", (entry.operation_id,))
    if result is not None and state in {
        OperationJournalState.SUCCEEDED,
        OperationJournalState.FAILED,
    }:
        if "accepted" in result and result["accepted"] is not (
            state is OperationJournalState.SUCCEEDED
        ):
            raise OperationJournalError("inconsistent_outcome", (entry.operation_id, state))
        if "state" in result and result["state"] != state.value:
            raise OperationJournalError("inconsistent_outcome", (entry.operation_id, state))
    return replace(
        entry, state=state, result=deepcopy(result), observation_time=datetime.now(UTC).isoformat()
    )


def canonical_maintenance_action(action: str) -> str:
    """Unify scheduled and interactive expiry without changing other identities."""
    return "snapshot_expiry" if action == "expire_snapshots" else action


def maintenance_action_aliases(action: str) -> tuple[str, ...]:
    """Persisted action spellings that must share a claim, including legacy rows."""
    if canonical_maintenance_action(action) == "snapshot_expiry":
        return ("snapshot_expiry", "expire_snapshots")
    return (action,)


def maintenance_operation_id(operation: str, table: str, ref: str, plan_token: str) -> str:
    """Canonical, durable journal id for one exact maintenance plan.

    Binding the id to the plan token lets a fresh plan for the same table and
    ref be applied again, while a second apply of the same plan replays or
    conflicts. Public so the CLI and HTTP surfaces resolve the same
    verification handle.
    """
    return f"{operation}:{table}:{ref}:{plan_token}"


def maintenance_plan_token(plan: Mapping[str, Any]) -> str:
    """Confirmation token for one maintenance plan.

    Compaction binds by its revision precondition rather than a plan hash, so
    a plan without a ``plan_token`` uses its ``before_revision``. The CLI and
    HTTP surfaces both derive the token here so they journal the same id.
    """
    token = plan.get("plan_token")
    if not token and plan.get("before_revision") is not None:
        token = plan["before_revision"]
    return "" if token is None else str(token)


def _maintenance_state(result: Mapping[str, Any]) -> OperationJournalState:
    failure = result.get("failure")
    outcome = failure.get("outcome") if isinstance(failure, Mapping) else None
    code = str(failure.get("code", "")) if isinstance(failure, Mapping) else ""
    if outcome == "unknown" or "outcome_unknown" in code:
        return OperationJournalState.UNKNOWN
    if result.get("status") in {"failed", "blocked"}:
        if result.get("accepted") is False and (
            outcome == "not_submitted" or result.get("executed") is False
        ):
            return OperationJournalState.FAILED
        if outcome != "not_submitted" and (
            result.get("retry_safe") is False
            or result.get("accepted") is True
            or result.get("executed") is True
        ):
            return OperationJournalState.UNKNOWN
        if result.get("accepted") is False:
            return OperationJournalState.FAILED
    if result.get("accepted") is True and result.get("status") in {"succeeded", "noop"}:
        return OperationJournalState.SUCCEEDED
    return OperationJournalState.UNKNOWN


def execute_maintenance(
    store: OperationJournalStore,
    *,
    operation: str,
    table: str,
    ref: str,
    plan_token: str,
    subject: str,
    execute: Callable[[str], MaintenanceOperationResult | Mapping[str, Any]],
    operation_id: str | None = None,
) -> OperationJournalEntry:
    """Claim and execute one maintenance plan, retaining uncertain submission fences.

    The adapter supplies provider invocation and translates errors/output. Neither
    an accepted submission nor a failed response proves a definitive outcome.
    Preserve returned evidence even when it cannot release the durable claim.
    Scheduled adapters may retain an existing run-bound verification handle.
    """
    if operation_id is None:
        operation_id = maintenance_operation_id(operation, table, ref, plan_token)
    journal = OperationJournal(store)
    entry = journal.claim(
        operation_id=operation_id,
        subject=subject,
        action=operation,
        target=table,
        plan_token=plan_token,
    )
    journal.submit(operation_id)
    try:
        result = execute(operation_id)
        evidence = dict(result) if isinstance(result, Mapping) else result.to_dict()
    except Exception as exc:
        journal.unknown(operation_id)
        raise OperationJournalError("apply_outcome_unknown", (operation_id,)) from exc
    state = _maintenance_state(evidence)
    if state is OperationJournalState.UNKNOWN:
        journal.unknown(operation_id, result=evidence)
    else:
        journal.complete(operation_id, state=state, result=evidence)
    return replace(entry, state=state, result=evidence)


class OperationJournal:
    """The sole command owner for claims, submission, outcomes and recovery.

    Adapters implement atomic compare-and-set, not outcome classification.
    Only a pre-submission lease can expire; submitted and unknown outcomes
    retain their exclusion until a definitive result is recorded.
    """

    def __init__(self, store: OperationJournalStore) -> None:
        self._store = store

    def claim(
        self,
        *,
        operation_id: str,
        subject: str,
        action: str,
        target: str,
        plan_token: str,
        claim_expiry: str | None = None,
    ) -> OperationJournalEntry:
        """Acquire a five-minute pre-submission lease for an exact binding."""
        entry = OperationJournalEntry(
            operation_id=operation_id,
            subject=subject,
            action=canonical_maintenance_action(action),
            target=target,
            plan_token=plan_token,
            state=OperationJournalState.CLAIMED,
            claim_expiry=claim_expiry or (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        )
        if not self._store.claim(entry):
            raise OperationJournalError("conflicting_claim", (operation_id, action, target))
        return entry

    def submit(self, operation_id: str) -> None:
        """Fence provider effects before invoking the provider."""
        self._transition(
            operation_id, OperationJournalState.CLAIMED, OperationJournalState.SUBMITTED
        )

    def reject(self, operation_id: str, *, result: dict[str, Any]) -> None:
        """Record a definitive pre-submission rejection."""
        self._transition(
            operation_id, OperationJournalState.CLAIMED, OperationJournalState.FAILED, result
        )

    def complete(
        self,
        operation_id: str,
        *,
        state: OperationJournalState,
        result: dict[str, Any],
    ) -> None:
        """Record an explicitly classified, definitive submitted outcome."""
        self._require_terminal(operation_id, state)
        self._transition(operation_id, OperationJournalState.SUBMITTED, state, result)

    def unknown(self, operation_id: str, *, result: dict[str, Any] | None = None) -> None:
        """Retain uncertainty and any available evidence without releasing the fence."""
        self._transition(
            operation_id, OperationJournalState.SUBMITTED, OperationJournalState.UNKNOWN, result
        )

    def reconcile(
        self,
        operation_id: str,
        *,
        state: OperationJournalState,
        result: dict[str, Any],
    ) -> None:
        """Resolve only UNKNOWN, using explicit definitive verification evidence."""
        self._require_terminal(operation_id, state)
        entry = self._store.read(operation_id)
        if (
            entry is not None
            and canonical_maintenance_action(entry.action) in {"compact", "snapshot_expiry"}
            and _maintenance_state(result) is not state
        ):
            raise OperationJournalError("unknown_outcome_blocks_replay", (operation_id,))
        self._transition(operation_id, OperationJournalState.UNKNOWN, state, result)

    @staticmethod
    def replay_block_reason(entry: OperationJournalEntry) -> str | None:
        """Explain whether a receipt is replayable, independent of caller binding."""
        if entry.state is OperationJournalState.UNKNOWN:
            return "unknown_outcome_blocks_replay"
        if entry.state not in {OperationJournalState.SUCCEEDED, OperationJournalState.FAILED}:
            return "operation_in_progress"
        if entry.result is None:
            return "missing_result"
        failure = entry.result.get("failure")
        if isinstance(failure, Mapping) and failure.get("reason") == "claim_expired":
            return "claim_expired"
        return None

    def replay(
        self,
        operation_id: str,
        *,
        subject: str,
        action: str,
        target: str,
        plan_token: str,
    ) -> Mapping[str, Any] | None:
        """Return an exact terminal receipt; active work is never a new operation."""
        entry = self._store.read(operation_id)
        if entry is None:
            return None
        if (
            entry.subject,
            canonical_maintenance_action(entry.action),
            entry.target,
            entry.plan_token,
        ) != (
            subject,
            canonical_maintenance_action(action),
            target,
            plan_token,
        ):
            raise OperationJournalError("binding_mismatch", (operation_id,))
        if reason := self.replay_block_reason(entry):
            raise OperationJournalError(reason, (operation_id,))
        return deepcopy(entry.result)

    @staticmethod
    def _require_terminal(operation_id: str, state: OperationJournalState) -> None:
        if state not in {OperationJournalState.SUCCEEDED, OperationJournalState.FAILED}:
            raise OperationJournalError("invalid_outcome", (operation_id, state))

    def _transition(
        self,
        operation_id: str,
        expected: OperationJournalState,
        state: OperationJournalState,
        result: dict[str, Any] | None = None,
    ) -> None:
        if not self._store.compare_and_set(operation_id, expected, state, result):
            code = (
                "unknown_operation"
                if self._store.read(operation_id) is None
                else "invalid_transition"
            )
            raise OperationJournalError(code, (operation_id, expected, state))
