"""Dataset durable state resolution and explicit test mode.

Binds a project to its registered ``dataset_state_store`` capability and owns
the durable-vs-test mode decision:

- ``durable`` (default): resolves the provider-owned store through the
  capability registry. The durable implementation lives provider-side
  (``phlo-postgres`` over the transactional settings service); core never
  imports it. With no provider registered this fails closed with guidance.
- ``memory`` (explicit test mode): a process-local in-memory compare-and-set
  store. It is never selected by default and exists only for tests and local
  experiments; it is not durable and shares no state across processes.

The mode is fixed by ``PHLO_DATASET_STATE_STORE`` (``durable`` | ``memory``)
or an explicit ``mode`` argument. This module is deliberately outside
:mod:`phlo.dataset` so the neutral Dataset core keeps its stdlib-only import
boundary.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Any

from phlo.dataset.migration import MigrationStore  # noqa: F401  (re-exported contract)
from phlo.dataset.models import (
    DATASET_STATE_SCHEMA_VERSION,
    CandidateRecord,
    DatasetRecord,
    DatasetStateRecord,
    TransitionAuditEvent,
    is_candidate_dataset_id,
)
from phlo.dataset.store import (
    CommittedAction,
    DatasetStateStore,
    StoreWrite,
    StoreWriteResult,
    StoreWriteStatus,
    state_store_namespace,
)

MODE_DURABLE = "durable"
MODE_MEMORY = "memory"
MODE_ENV_VAR = "PHLO_DATASET_STATE_STORE"


class DatasetStoreResolutionError(RuntimeError):
    """Raised when no durable dataset state store capability is registered."""


def resolve_store_mode(explicit: str | None = None) -> str:
    """Return the effective store mode: explicit argument, env, or ``durable``."""
    mode = explicit or os.environ.get(MODE_ENV_VAR) or MODE_DURABLE
    if mode not in {MODE_DURABLE, MODE_MEMORY}:
        raise ValueError(
            f"Unknown dataset state store mode {mode!r}; expected {MODE_DURABLE!r} or {MODE_MEMORY!r}."
        )
    return mode


def resolve_dataset_state_store(
    project_root: str,
    *,
    mode: str | None = None,
) -> DatasetStateStore:
    """Resolve the store for one project in the effective mode.

    Durable mode resolves the ``dataset_state_store`` capability family; the
    returned store also implements :class:`phlo.dataset.migration.MigrationStore`.
    """
    mode = resolve_store_mode(mode)
    if mode == MODE_MEMORY:
        return memory_store()
    from phlo.capabilities import resolve_capability

    result = resolve_capability("dataset_state_store")
    if result is None:
        raise DatasetStoreResolutionError(
            "No durable dataset state store is registered. Install a provider "
            "(phlo-postgres) or set PHLO_DATASET_STATE_STORE=memory for the "
            "explicit test mode."
        )
    factory = result.provider
    return factory.store(project_root)


def memory_store() -> MemoryDatasetStateStore:
    """Return the process-local memory-mode store singleton (explicit test mode)."""
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryDatasetStateStore()
    return _memory_store


_memory_store: MemoryDatasetStateStore | None = None


def reset_memory_store() -> None:
    """Clear the memory-mode singleton (test helper)."""
    global _memory_store
    _memory_store = None


class MemoryDatasetStateStore:
    """In-memory Dataset state store for the explicit test mode.

    Implements the same compare-and-set, audit, and migration-transaction
    contract as the provider-owned durable store, over a process-local dict.
    Not durable, never shared across processes, never selected by default.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._actions: dict[str, dict[str, dict[str, Any]]] = {}
        self._audit: list[dict[str, Any]] = []
        self._config: dict[str, Any] = {}
        self._migrations: dict[str, dict[str, Any]] = {}
        self._discards: list[dict[str, Any]] = []

    # -- Reads ------------------------------------------------------------

    def load(self, dataset_id: str) -> DatasetStateRecord | None:
        with self._lock:
            payload = self._records.get(dataset_id)
            return _payload_record(payload)

    def committed_action(self, dataset_id: str, action_id: str) -> CommittedAction | None:
        with self._lock:
            entry = self._actions.get(dataset_id, {}).get(action_id)
            return CommittedAction(**entry) if entry else None

    def committed_migration(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            migration = self._migrations.get(action_id)
            return dict(migration) if migration else None

    def workflow_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def audit_events(self) -> tuple[TransitionAuditEvent, ...]:
        with self._lock:
            return tuple(TransitionAuditEvent(**event) for event in self._audit)

    # -- Compare-and-set ---------------------------------------------------

    def compare_and_set(
        self,
        *,
        writes: tuple[StoreWrite, ...],
        action_id: str,
        action: str,
        fingerprint: str,
    ) -> StoreWriteResult:
        with self._lock:
            last = writes[-1]
            committed = self._actions.get(last.record_id, {}).get(action_id)
            if committed is not None:
                if committed["fingerprint"] == fingerprint:
                    stored = tuple(
                        record
                        for record in (
                            _payload_record(self._records.get(w.record_id)) for w in writes
                        )
                        if record is not None
                    )
                    return StoreWriteResult(
                        status=StoreWriteStatus.REPLAYED,
                        records=stored,
                        committed_fingerprint=committed["fingerprint"],
                        detail="Transition already committed; replaying the stored outcome.",
                    )
                return StoreWriteResult(
                    status=StoreWriteStatus.ACTION_CONFLICT,
                    detail=f"action_id {action_id!r} was already committed with a different request.",
                )
            for write in writes:
                current = self._records.get(write.record_id)
                current_state = _state_of(_payload_record(current)) if current else "open"
                if current_state != write.expected_state:
                    return StoreWriteResult(
                        status=StoreWriteStatus.PRECONDITION_FAILED,
                        detail=(
                            f"{write.record_id} moved from {write.expected_state!r} "
                            f"to {current_state!r}"
                        ),
                    )
            committed_records = []
            for write in writes:
                self._records[write.record_id] = write.next_record.to_read_model()
                committed_records.append(write.next_record)
            self._actions.setdefault(last.record_id, {})[action_id] = {
                "action_id": action_id,
                "resource_id": last.record_id,
                "action": action,
                "fingerprint": fingerprint,
                "outcome_status": StoreWriteStatus.COMMITTED.value,
                "after_state": _state_of(committed_records[-1]),
            }
            return StoreWriteResult(
                status=StoreWriteStatus.COMMITTED,
                records=tuple(committed_records),
                committed_fingerprint=fingerprint,
                detail=f"{action!r} committed for {last.record_id}.",
            )

    def append_audit(self, event: TransitionAuditEvent) -> None:
        with self._lock:
            self._audit.append(event.to_read_model())

    def write_workflow_config(
        self,
        *,
        config: Mapping[str, Any],
        actor: str | None = None,
        scope: str | None = None,
    ) -> None:
        """Persist the workflow configuration atomically with an audit event."""
        with self._lock:
            self._config = dict(config)
            self._audit.append(
                TransitionAuditEvent(
                    actor=actor,
                    scope=scope,
                    action_id=f"workflow-config-{len(self._audit)}",
                    resource_id="overlay",
                    action="write-workflow-config",
                    before_state=None,
                    after_state="updated",
                    outcome="committed",
                    detail="Updated the Dataset workflow configuration.",
                ).to_read_model()
            )

    # -- Migration transaction ----------------------------------------------

    def commit_migration(
        self,
        *,
        records: tuple[DatasetStateRecord, ...],
        config: Mapping[str, Any],
        action_id: str,
        fingerprint: str,
        actor: str | None = None,
        scope: str | None = None,
    ) -> StoreWriteResult:
        with self._lock:
            committed = self._migrations.get(action_id)
            if committed is not None:
                if committed["fingerprint"] == fingerprint:
                    stored = tuple(
                        record
                        for record in (
                            _payload_record(self._records.get(record_id))
                            for record_id in committed["record_ids"]
                        )
                        if record is not None
                    )
                    return StoreWriteResult(
                        status=StoreWriteStatus.REPLAYED,
                        records=stored,
                        committed_fingerprint=committed["fingerprint"],
                        detail="Overlay migration already committed; replaying the stored result.",
                    )
                return StoreWriteResult(
                    status=StoreWriteStatus.ACTION_CONFLICT,
                    detail=f"Migration action_id {action_id!r} was committed with a different plan.",
                )
            for record in records:
                if record.dataset_id in self._records:
                    return StoreWriteResult(
                        status=StoreWriteStatus.ACTION_CONFLICT,
                        detail=(
                            f"{record.dataset_id} already exists in the durable store; "
                            "refusing to overwrite it during overlay migration."
                        ),
                    )
            for record in records:
                self._records[record.dataset_id] = record.to_read_model()
            self._config = dict(config)
            self._migrations[action_id] = {
                "fingerprint": fingerprint,
                "record_ids": [record.dataset_id for record in records],
                "actor": actor,
                "scope": scope,
            }
            self._audit.append(
                TransitionAuditEvent(
                    actor=actor,
                    scope=scope,
                    action_id=action_id,
                    resource_id="overlay",
                    action="migrate-overlay",
                    before_state=None,
                    after_state="imported",
                    outcome="committed",
                    detail=f"Imported {len(records)} legacy overlay record(s).",
                ).to_read_model()
            )
            return StoreWriteResult(
                status=StoreWriteStatus.COMMITTED,
                records=tuple(records),
                committed_fingerprint=fingerprint,
                detail=f"Imported {len(records)} legacy overlay record(s).",
            )

    def record_discard(
        self,
        *,
        source_digest: str,
        plan_digest: str,
        actor: str | None = None,
        scope: str | None = None,
    ) -> None:
        with self._lock:
            discard_id = f"overlay-discard-{source_digest}"
            if any(entry["action_id"] == discard_id for entry in self._discards):
                return
            self._discards.append(
                {
                    "action_id": discard_id,
                    "source_digest": source_digest,
                    "plan_digest": plan_digest,
                }
            )
            self._audit.append(
                TransitionAuditEvent(
                    actor=actor,
                    scope=scope,
                    action_id=discard_id,
                    resource_id="overlay",
                    action="discard-overlay",
                    before_state=None,
                    after_state=None,
                    outcome="discarded",
                    detail=f"Discarded the planned overlay import (source {source_digest[:12]}).",
                ).to_read_model()
            )


def _payload_record(payload: dict[str, Any] | None) -> DatasetStateRecord | None:
    """Rebuild one typed record from its stored read model, or None."""
    if not payload:
        return None
    dataset_id = payload["dataset_id"]
    if is_candidate_dataset_id(dataset_id):
        return CandidateRecord(**payload)
    return DatasetRecord(**payload)


def _state_of(record: DatasetStateRecord | None) -> str | None:
    return record.current_state if record else None


STATE_SCHEMA_VERSION = DATASET_STATE_SCHEMA_VERSION

__all__ = [
    "MODE_DURABLE",
    "MODE_ENV_VAR",
    "MODE_MEMORY",
    "DatasetStoreResolutionError",
    "MemoryDatasetStateStore",
    "memory_store",
    "reset_memory_store",
    "resolve_dataset_state_store",
    "resolve_store_mode",
    "state_store_namespace",
]
