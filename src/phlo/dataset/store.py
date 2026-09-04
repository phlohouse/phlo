"""Neutral durable state-store capability for Dataset transitions.

The accepted store protocol is a compare-and-set over the durable,
project-scoped collection: every mutation runs inside the store's transaction,
commits an expected current state to a new record, and records the client
``action_id``. Replay of a committed ``action_id`` with an identical request
returns the original outcome; a conflicting identity or a mismatched expected
state fails without writing. Implementations are registered provider-free
(the ``dataset_state_store`` capability family); core never imports one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from phlo.dataset.models import (
    CandidateRecord,
    DatasetRecord,
    DatasetStateRecord,
    TransitionAuditEvent,
)

WORKFLOW_NAMESPACE_PREFIX = "observatory.dataset_workflow."


class StoreWriteStatus(StrEnum):
    """Result of a store-level compare-and-set."""

    COMMITTED = "committed"
    REPLAYED = "replayed"
    ACTION_CONFLICT = "action_conflict"
    PRECONDITION_FAILED = "precondition_failed"


@dataclass(frozen=True, slots=True)
class StoreWrite:
    """One record write inside an atomic compare-and-set batch."""

    record_id: str
    expected_state: str | None
    next_record: DatasetStateRecord


@dataclass(frozen=True, slots=True)
class StoreWriteResult:
    """Outcome of one atomic compare-and-set batch."""

    status: StoreWriteStatus
    records: tuple[DatasetStateRecord, ...] = ()
    committed_fingerprint: str | None = field(default=None)
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CommittedAction:
    """A transition already committed for one record, kept for replay."""

    action_id: str
    resource_id: str
    action: str
    fingerprint: str
    outcome_status: str
    after_state: str | None = None


@runtime_checkable
class DatasetStateStore(Protocol):
    """Capability interface for the durable Dataset workflow store."""

    def load(self, dataset_id: str) -> DatasetStateRecord | None:
        """Return the current record for one canonical Dataset ID, or None."""
        ...

    def committed_action(self, dataset_id: str, action_id: str) -> CommittedAction | None:
        """Return the action already committed for this record and key, if any."""
        ...

    def compare_and_set(
        self,
        *,
        writes: tuple[StoreWrite, ...],
        action_id: str,
        action: str,
        fingerprint: str,
    ) -> StoreWriteResult:
        """Atomically commit a batch of record writes or explain why it failed.

        The store runs the whole batch inside one transaction (single
        serialized writer per namespace). Each write is a compare-and-set on
        its record's current state (``open`` when the record does not exist).
        The commit records ``action_id`` and ``fingerprint`` so retries and
        replays resolve to the committed outcome.
        """
        ...

    def append_audit(self, event: TransitionAuditEvent) -> None:
        """Append one audit event for a transition attempt."""
        ...


def state_store_namespace(project_root: str) -> str:
    """Return the deterministic project-scoped store namespace."""
    digest = hashlib.sha256(project_root.encode("utf-8")).hexdigest()
    return f"{WORKFLOW_NAMESPACE_PREFIX}{digest}"


__all__ = [
    "CommittedAction",
    "CandidateRecord",
    "DatasetRecord",
    "DatasetStateStore",
    "StoreWrite",
    "StoreWriteResult",
    "StoreWriteStatus",
    "WORKFLOW_NAMESPACE_PREFIX",
    "state_store_namespace",
]
