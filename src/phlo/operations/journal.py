"""Durable operation journal (ADR 0049 §1).

Provider-neutral claim/binding/state/result types and the atomic
cross-process state machine. Core owns the state transitions; production
persistence is a provider-owned adapter (PostgreSQL), never core SQL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


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
    """A durable, cross-process operation journal store.

    ``claim`` atomically inserts the entry if no unexpired claim exists for
    the same (action, target). ``transition`` moves the state forward. Both
    raise on conflicting binding or stale/expired claims.
    """

    def claim(self, entry: OperationJournalEntry) -> bool:
        """Atomically insert; return False if an unexpired conflicting claim exists."""
        ...

    def transition(
        self, operation_id: str, state: OperationJournalState, result: dict[str, Any] | None = None
    ) -> bool:
        """Move the operation forward; return False if the state is unknown."""
        ...

    def read(self, operation_id: str) -> OperationJournalEntry | None:
        """Read one entry by operation_id."""
        ...


class InMemoryOperationJournalStore:
    """Test/development-only in-memory journal store."""

    def __init__(self) -> None:
        self.entries: dict[str, OperationJournalEntry] = {}

    def claim(self, entry: OperationJournalEntry) -> bool:
        existing = self.entries.get(entry.operation_id)
        if existing is not None and existing.state is not OperationJournalState.SUCCEEDED:
            return False
        self.entries[entry.operation_id] = entry
        return True

    def transition(
        self, operation_id: str, state: OperationJournalState, result: dict[str, Any] | None = None
    ) -> bool:
        entry = self.entries.get(operation_id)
        if entry is None:
            return False
        self.entries[operation_id] = OperationJournalEntry(
            operation_id=entry.operation_id,
            subject=entry.subject,
            action=entry.action,
            target=entry.target,
            plan_token=entry.plan_token,
            state=state,
            claim_expiry=entry.claim_expiry,
            result=result,
            observation_time=entry.observation_time,
        )
        return True

    def read(self, operation_id: str) -> OperationJournalEntry | None:
        return self.entries.get(operation_id)


def claim_operation(
    store: OperationJournalStore,
    *,
    operation_id: str,
    subject: str,
    action: str,
    target: str,
    plan_token: str,
) -> OperationJournalEntry:
    """Atomically claim an operation, or raise on conflict."""
    entry = OperationJournalEntry(
        operation_id=operation_id,
        subject=subject,
        action=action,
        target=target,
        plan_token=plan_token,
        state=OperationJournalState.CLAIMED,
    )
    if not store.claim(entry):
        raise OperationJournalError("conflicting_claim", (operation_id, action, target))
    return entry


def mark_submitted(store: OperationJournalStore, operation_id: str) -> None:
    """Record that the provider call was issued (before the result is known)."""
    if not store.transition(operation_id, OperationJournalState.SUBMITTED):
        raise OperationJournalError("unknown_operation", (operation_id,))


def complete_operation(
    store: OperationJournalStore, operation_id: str, result: dict[str, Any]
) -> None:
    """Record a definitive provider result."""
    state = (
        OperationJournalState.SUCCEEDED if result.get("accepted") else OperationJournalState.FAILED
    )
    if not store.transition(operation_id, state, result):
        raise OperationJournalError("unknown_operation", (operation_id,))


def mark_unknown(store: OperationJournalStore, operation_id: str) -> None:
    """Record that the provider call was issued but the outcome is unknown.

    Blocks automatic replay and a new key until explicit reconciliation.
    """
    if not store.transition(operation_id, OperationJournalState.UNKNOWN):
        raise OperationJournalError("unknown_operation", (operation_id,))


def reconcile_unknown(
    store: OperationJournalStore, operation_id: str, result: dict[str, Any]
) -> None:
    """Explicitly reconcile an unknown outcome after operator-driven verification."""
    if not store.transition(
        operation_id,
        OperationJournalState.SUCCEEDED if result.get("accepted") else OperationJournalState.FAILED,
        result,
    ):
        raise OperationJournalError("unknown_operation", (operation_id,))


def read_or_replay(store: OperationJournalStore, operation_id: str) -> Mapping[str, Any] | None:
    """Read a stored result for idempotent replay; None if the operation is new."""
    entry = store.read(operation_id)
    if entry is None:
        return None
    if entry.state is OperationJournalState.UNKNOWN:
        raise OperationJournalError("unknown_outcome_blocks_replay", (operation_id,))
    return entry.result
