"""Durable operation journal (ADR 0049 §1).

Provider-neutral claim/binding/state/result types and the atomic
cross-process state machine. Core owns the state transitions; production
persistence is a provider-owned adapter (PostgreSQL), never core SQL.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
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
        entry = replace(entry, action=canonical_maintenance_action(entry.action))
        existing = self.entries.get(entry.operation_id)
        if existing is not None and existing.state is not OperationJournalState.SUCCEEDED:
            return False
        if entry.action == "snapshot_expiry" and any(
            canonical_maintenance_action(record.action) == entry.action
            and record.target == entry.target
            and record.state
            in {
                OperationJournalState.CLAIMED,
                OperationJournalState.SUBMITTED,
                OperationJournalState.UNKNOWN,
            }
            for record in self.entries.values()
        ):
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
    entry = claim_operation(
        store,
        operation_id=operation_id,
        subject=subject,
        action=operation,
        target=table,
        plan_token=plan_token,
    )
    mark_submitted(store, operation_id)
    try:
        result = execute(operation_id)
        evidence = (
            result.to_dict() if isinstance(result, MaintenanceOperationResult) else dict(result)
        )
    except Exception as exc:
        mark_unknown(store, operation_id)
        raise OperationJournalError("apply_outcome_unknown", (operation_id,)) from exc
    state = _maintenance_state(evidence)
    if not store.transition(operation_id, state, evidence):
        raise OperationJournalError("unknown_operation", (operation_id,))
    return replace(entry, state=state, result=evidence)


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
        action=canonical_maintenance_action(action),
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
    entry = store.read(operation_id)
    if entry is not None and canonical_maintenance_action(entry.action) in {
        "compact",
        "snapshot_expiry",
    }:
        state = _maintenance_state(result)
        if state is OperationJournalState.UNKNOWN:
            raise OperationJournalError("unknown_outcome_blocks_replay", (operation_id,))
    else:
        # Backup/restore reconciliation retains its accepted-result contract.
        state = (
            OperationJournalState.SUCCEEDED
            if result.get("accepted")
            else OperationJournalState.FAILED
        )
    if not store.transition(
        operation_id,
        state,
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
