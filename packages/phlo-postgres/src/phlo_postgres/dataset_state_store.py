"""Durable Dataset state store capability for phlo-postgres.

Provider-owned implementation of the neutral
:class:`phlo.dataset.store.DatasetStateStore` compare-and-set contract and the
:class:`phlo.dataset.migration.MigrationStore` overlay-migration transaction.
The backend is a transactional,
project-scoped settings-backed store (the ``observatory_durable_state.py``
pattern) -- one settings record per hashed project namespace, mutated through
the settings service's single serialized-writer transaction (PostgreSQL
advisory-lock ``mutate``). Core never imports this package; the store reaches
it through the ``dataset_state_store`` capability family.

The whole Dataset workflow namespace -- records, committed ``action_id``s, the
append-only audit stream, the workflow configuration, and the overlay
migration journal -- lives in that one record, so every mutation (transition,
migration apply, discard) is atomic with respect to idempotency, audit, and
state: no interleaving of two workers can produce split-brain state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from phlo.capabilities import DatasetStateStoreSpec
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
    StoreWrite,
    StoreWriteResult,
    StoreWriteStatus,
    state_store_namespace,
)
from phlo.logging import get_logger
from phlo.plugins.observatory_settings import (
    SettingsScope,
    SettingsStore,
    StorageCorruptionError,
    get_settings_service,
)

logger = get_logger(__name__)

STATE_SCHEMA_VERSION = DATASET_STATE_SCHEMA_VERSION
"""Payload schema version of the durable Dataset workflow collection."""


class SettingsDatasetStateStore:
    """Durable Dataset state store over the transactional settings service.

    ``settings_store`` is any neutral :class:`SettingsStore` implementation
    (resolved through the ``settings_store`` capability); this class adds the
    Dataset compare-and-set and migration-transaction semantics on top of its
    serialized ``mutate`` transaction. One instance is bound to one project's
    namespace via :meth:`DatasetStateStoreProvider.store`.
    """

    def __init__(self, settings_store: SettingsStore, namespace: str) -> None:
        self._settings = settings_store
        self._namespace = namespace

    # -- Reads ------------------------------------------------------------

    def load(self, dataset_id: str) -> DatasetStateRecord | None:
        state = self._read_state()
        return _payload_record(state["records"].get(dataset_id))

    def committed_action(self, dataset_id: str, action_id: str) -> CommittedAction | None:
        entry = self._read_state()["actions"].get(dataset_id, {}).get(action_id)
        return CommittedAction(**entry) if entry else None

    def workflow_config(self) -> dict[str, Any]:
        """Return the workflow configuration imported with the overlay."""
        return dict(self._read_state()["config"])

    def audit_events(self) -> tuple[TransitionAuditEvent, ...]:
        """Return the append-only audit stream, oldest first."""
        return tuple(TransitionAuditEvent(**event) for event in self._read_state()["audit"])

    def committed_migration(self, action_id: str) -> dict[str, Any] | None:
        migration = self._read_state()["migrations"].get(action_id)
        return dict(migration) if migration else None

    # -- Compare-and-set ----------------------------------------------------

    def compare_and_set(
        self,
        *,
        writes: tuple[StoreWrite, ...],
        action_id: str,
        action: str,
        fingerprint: str,
    ) -> StoreWriteResult:
        def apply(state: dict[str, Any] | None) -> dict[str, Any]:
            current = _validated_state(state)
            last = writes[-1]
            committed = current["actions"].get(last.record_id, {}).get(action_id)
            if committed is not None:
                if committed["fingerprint"] == fingerprint:
                    self._result = StoreWriteResult(
                        status=StoreWriteStatus.REPLAYED,
                        records=tuple(
                            record
                            for record in (
                                _payload_record(current["records"].get(w.record_id)) for w in writes
                            )
                            if record is not None
                        ),
                        committed_fingerprint=committed["fingerprint"],
                        detail="Transition already committed; replaying the stored outcome.",
                    )
                    return current
                self._result = StoreWriteResult(
                    status=StoreWriteStatus.ACTION_CONFLICT,
                    detail=(
                        f"action_id {action_id!r} was already committed with a different request."
                    ),
                )
                return current

            for write in writes:
                existing = current["records"].get(write.record_id)
                existing_record = _payload_record(existing) if existing else None
                current_state = existing_record.current_state if existing_record else "open"
                if current_state != write.expected_state:
                    self._result = StoreWriteResult(
                        status=StoreWriteStatus.PRECONDITION_FAILED,
                        detail=(
                            f"{write.record_id} moved from {write.expected_state!r} "
                            f"to {current_state!r}"
                        ),
                    )
                    return current

            next_state = _copy_state(current)
            for write in writes:
                next_state["records"][write.record_id] = write.next_record.to_read_model()
            next_state["actions"].setdefault(last.record_id, {})[action_id] = {
                "action_id": action_id,
                "resource_id": last.record_id,
                "action": action,
                "fingerprint": fingerprint,
                "outcome_status": StoreWriteStatus.COMMITTED.value,
                "after_state": _record_state(writes[-1].next_record),
            }
            self._result = StoreWriteResult(
                status=StoreWriteStatus.COMMITTED,
                records=tuple(w.next_record for w in writes),
                committed_fingerprint=fingerprint,
                detail=f"{action!r} committed for {last.record_id}.",
            )
            return next_state

        self._mutate(apply)
        return self._result

    def append_audit(self, event: TransitionAuditEvent) -> None:
        def apply(state: dict[str, Any] | None) -> dict[str, Any]:
            current = _validated_state(state)
            next_state = _copy_state(current)
            next_state["audit"].append(event.to_read_model())
            return next_state

        self._mutate(apply)

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
        """Commit every imported record plus the workflow config atomically.

        Runs inside one settings transaction: idempotency check, record
        insertion, configuration import, and audit events all land together, so
        a crash or a second worker can never observe half an import.
        """

        def apply(state: dict[str, Any] | None) -> dict[str, Any]:
            current = _validated_state(state)
            committed = current["migrations"].get(action_id)
            if committed is not None:
                if committed["fingerprint"] == fingerprint:
                    self._result = StoreWriteResult(
                        status=StoreWriteStatus.REPLAYED,
                        records=tuple(
                            record
                            for record in (
                                _payload_record(current["records"].get(record_id))
                                for record_id in committed["record_ids"]
                            )
                            if record is not None
                        ),
                        committed_fingerprint=committed["fingerprint"],
                        detail="Overlay migration already committed; replaying the stored result.",
                    )
                    return current
                self._result = StoreWriteResult(
                    status=StoreWriteStatus.ACTION_CONFLICT,
                    detail=(
                        f"Migration action_id {action_id!r} was committed with a different plan."
                    ),
                )
                return current

            for record in records:
                if record.dataset_id in current["records"]:
                    self._result = StoreWriteResult(
                        status=StoreWriteStatus.ACTION_CONFLICT,
                        detail=(
                            f"{record.dataset_id} already exists in the durable store; "
                            "refusing to overwrite it during overlay migration."
                        ),
                    )
                    return current

            next_state = _copy_state(current)
            for record in records:
                next_state["records"][record.dataset_id] = record.to_read_model()
            next_state["config"] = dict(config)
            next_state["migrations"][action_id] = {
                "fingerprint": fingerprint,
                "record_ids": [record.dataset_id for record in records],
                "actor": actor,
                "scope": scope,
            }
            next_state["audit"].append(
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
            self._result = StoreWriteResult(
                status=StoreWriteStatus.COMMITTED,
                records=tuple(records),
                committed_fingerprint=fingerprint,
                detail=f"Imported {len(records)} legacy overlay record(s).",
            )
            return next_state

        self._mutate(apply)
        return self._result

    def record_discard(
        self,
        *,
        source_digest: str,
        plan_digest: str,
        actor: str | None = None,
        scope: str | None = None,
    ) -> None:
        """Record the explicit discard of one planned overlay import.

        Audited and idempotent: the discard is journaled once per source
        digest; the legacy file itself is never touched.
        """
        discard_id = f"overlay-discard-{source_digest}"

        def apply(state: dict[str, Any] | None) -> dict[str, Any]:
            current = _validated_state(state)
            if any(entry["action_id"] == discard_id for entry in current["discards"]):
                return current
            next_state = _copy_state(current)
            next_state["discards"].append(
                {
                    "action_id": discard_id,
                    "source_digest": source_digest,
                    "plan_digest": plan_digest,
                }
            )
            next_state["audit"].append(
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
            return next_state

        self._mutate(apply)

    # -- Internals ----------------------------------------------------------

    _result: StoreWriteResult

    def _read_state(self) -> dict[str, Any]:
        record = self._settings.get(SettingsScope.GLOBAL, self._namespace)
        return _validated_state(record.settings if record else None)

    def _mutate(self, apply) -> None:
        self._settings.mutate(SettingsScope.GLOBAL, self._namespace, apply)


class DatasetStateStoreProvider:
    """Capability factory binding one project's root to its durable store."""

    def store(self, project_root: str) -> SettingsDatasetStateStore:
        """Return the durable store for one project's hashed namespace."""
        return SettingsDatasetStateStore(
            settings_store=get_settings_service(),
            namespace=state_store_namespace(project_root),
        )


def get_dataset_state_stores() -> list[DatasetStateStoreSpec]:
    """Return capability specs for the PostgreSQL-backed Dataset state store."""
    return [DatasetStateStoreSpec(name="postgres", provider=DatasetStateStoreProvider())]


def _validated_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None:
        return _empty_state()
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        logger.error("dataset_state_corrupt", location="schema_version")
        raise StorageCorruptionError("Dataset durable state is unavailable")
    for key, kind in (
        ("records", dict),
        ("actions", dict),
        ("audit", list),
        ("config", dict),
        ("migrations", dict),
        ("discards", list),
    ):
        if not isinstance(state.get(key), kind):
            logger.error("dataset_state_corrupt", location=key)
            raise StorageCorruptionError("Dataset durable state is unavailable")
    return state


def _copy_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": state["schema_version"],
        "records": dict(state["records"]),
        "actions": {key: dict(value) for key, value in state["actions"].items()},
        "audit": list(state["audit"]),
        "config": dict(state["config"]),
        "migrations": dict(state["migrations"]),
        "discards": list(state["discards"]),
    }


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "records": {},
        "actions": {},
        "audit": [],
        "config": {},
        "migrations": {},
        "discards": [],
    }


def _record_state(record: DatasetStateRecord) -> str:
    """Return the state string a compare-and-set compares for one record."""
    return record.current_state


def _payload_record(payload: dict[str, Any] | None) -> DatasetStateRecord | None:
    """Rebuild one typed record from its stored read model, or None."""
    if not payload:
        return None
    try:
        dataset_id = payload["dataset_id"]
        if is_candidate_dataset_id(dataset_id):
            return CandidateRecord(**payload)
        return DatasetRecord(**payload)
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("dataset_state_corrupt", location="record_payload", error=str(exc))
        raise StorageCorruptionError("Dataset durable state is unavailable") from exc
