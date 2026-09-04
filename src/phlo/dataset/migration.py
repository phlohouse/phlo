"""Read-only planner and migration contract for the legacy Observatory overlay.

Imports the flocked ``.phlo/observatory/dataset_workflow.json`` overlay into the
durable Dataset state store through an explicit plan → apply → discard
workflow, never automatically. The planner is pure and read-only: it maps each
legacy record through one deterministic import-or-reject rule and never
touches a store.

Migration is exactly-once and idempotent by a content-derived ``action_id``
(``overlay-import-<source_digest>``): a digest-confirmed apply commits every
imported record plus the workflow configuration inside one store transaction,
records its own audit events, and replays the stored result unchanged when the
same source digest is applied again. The legacy file is never mutated or
deleted; after import the durable store is the only state any surface reads.

``schema_version`` is the durable collection payload version (2, from
:data:`phlo.dataset.models.DATASET_STATE_SCHEMA_VERSION`). Migration stamps
imported records with it and does **not** re-stamp it when policy versions
change. The live policy is identified by ``policy_version``, which every
transition commit stamps onto the record; imported legacy records carry
``policy_version: None`` (pre-policy-era) until their first post-migration
transition. A legacy file whose ``schema_version`` is ahead of the store fails
closed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from phlo.dataset.models import (
    DATASET_STATE_SCHEMA_VERSION,
    CandidateRecord,
    DatasetRecord,
    DatasetStateRecord,
    PublicationState,
    WorkflowState,
    candidate_dataset_id,
    dataset_table_id,
    is_candidate_dataset_id,
)

MIGRATION_ACTION = "migrate-overlay"
DISCARD_ACTION = "discard-overlay"
OVERLAY_RESOURCE_ID = "overlay"

_ENTRY_IMPORT = "import"
_ENTRY_REJECT = "reject"

_PUBLICATION_STATES = frozenset(state.value for state in PublicationState)


class LegacyOverlayError(ValueError):
    """Raised when a legacy overlay fails closed and cannot be planned."""


def source_file_digest(path: str) -> str:
    """Return the sha256 hex digest of the legacy overlay file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_action_id(source_digest: str) -> str:
    """Return the content-derived idempotency key for one overlay import."""
    return f"overlay-import-{source_digest}"


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    """One legacy record's deterministic import-or-reject outcome."""

    record_id: str
    action: str
    reason: str = ""
    record: DatasetStateRecord | None = None

    @property
    def imported(self) -> bool:
        return self.action == _ENTRY_IMPORT

    def to_read_model(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "action": self.action,
            "reason": self.reason,
            "record": self.record.to_read_model() if self.record else None,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Deterministic, read-only migration plan over one legacy overlay."""

    source_digest: str
    entries: tuple[MigrationEntry, ...]
    config: dict[str, Any] = field(default_factory=dict)
    schema_version: int = DATASET_STATE_SCHEMA_VERSION

    @property
    def imports(self) -> tuple[MigrationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.imported)

    @property
    def rejections(self) -> tuple[MigrationEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.imported)

    @property
    def records(self) -> tuple[DatasetStateRecord, ...]:
        return tuple(entry.record for entry in self.imports if entry.record is not None)

    def plan_digest(self) -> str:
        """Stable digest of the plan's full content, used to confirm apply/discard."""
        payload = {
            "schema_version": self.schema_version,
            "source_digest": self.source_digest,
            "config": self.config,
            "entries": [entry.to_read_model() for entry in self.entries],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_read_model(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_digest": self.source_digest,
            "plan_digest": self.plan_digest(),
            "config": self.config,
            "entries": [entry.to_read_model() for entry in self.entries],
            "import_count": len(self.imports),
            "rejection_count": len(self.rejections),
        }


def plan_migration(payload: Mapping[str, Any], *, source_digest: str = "") -> MigrationPlan:
    """Plan the import of one legacy overlay payload without touching any store.

    Every record is mapped through one deterministic rule (see module docstring);
    a record without a deterministic rule is rejected with a reason rather than
    guessed. A promoted candidate yields its candidate record plus its promoted
    ``<table_id>`` record; a legacy ``datasets`` entry for the same table merges
    over the candidate's fields (deterministic precedence: the datasets entry
    wins for ``publication_state``, ``approval_state``, and ``owner`` when
    present). Whole-payload problems -- a non-mapping payload, a
    ``schema_version`` ahead of the store, or a malformed ``config`` -- fail
    closed by raising :class:`LegacyOverlayError`.
    """
    if not isinstance(payload, Mapping):
        raise LegacyOverlayError("Legacy overlay must be a JSON object.")

    schema_version = payload.get("schema_version", DATASET_STATE_SCHEMA_VERSION - 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise LegacyOverlayError(
            f"Legacy overlay schema_version must be an integer: {schema_version!r}"
        )
    if schema_version > DATASET_STATE_SCHEMA_VERSION:
        raise LegacyOverlayError(
            f"Legacy overlay schema_version {schema_version} is ahead of the "
            f"store's {DATASET_STATE_SCHEMA_VERSION}; refusing to import."
        )

    datasets = _section(payload, "datasets")
    candidates = _section(payload, "candidates")
    config = _plan_config(payload.get("config"))

    candidate_results = dict(_candidate_entries(candidates))
    dataset_results = dict(_dataset_entries(datasets))

    promoted_candidates: dict[str, CandidateRecord] = {}
    for entry in candidate_results.values():
        if (
            entry.imported
            and isinstance(entry.record, CandidateRecord)
            and entry.record.state == WorkflowState.PROMOTED.value
        ):
            promoted_candidates[entry.record.table_id] = entry.record

    entries: dict[str, MigrationEntry] = {}
    for dataset_id, entry in dataset_results.items():
        candidate = promoted_candidates.pop(dataset_id, None)
        entries[dataset_id] = _merge_promoted_sources(dataset_id, candidate, entry)

    # A promoted candidate with no legacy datasets entry still yields its
    # promoted <table_id> record so promoted identity and publication state
    # survive migration in both durable records.
    for dataset_id, candidate in promoted_candidates.items():
        publication_state = candidate.publication_state or PublicationState.DRAFT.value
        promoted = DatasetRecord(
            dataset_id=dataset_id,
            table_id=candidate.table_id,
            publication_state=publication_state,
            owner=candidate.owner,
            approval_state=candidate.approval_state,
            migration_note=candidate.migration_note,
            legacy_fields=candidate.legacy_fields,
        )
        entries[dataset_id] = MigrationEntry(
            record_id=dataset_id, action=_ENTRY_IMPORT, record=promoted
        )

    # Candidate entries key a different ID space (``candidate:<table_id>``)
    # from promoted records, so they never collide with ``entries``.
    for entry in candidate_results.values():
        entries[entry.record_id] = entry

    ordered = [entries[key] for key in sorted(entries)]
    return MigrationPlan(source_digest=source_digest, entries=tuple(ordered), config=config)


def _merge_promoted_sources(
    dataset_id: str,
    candidate: CandidateRecord | None,
    dataset_entry: MigrationEntry,
) -> MigrationEntry:
    """Merge a legacy datasets entry with its promoted candidate source."""
    if not dataset_entry.imported or dataset_entry.record is None:
        return dataset_entry
    dataset_record = dataset_entry.record
    if not isinstance(dataset_record, DatasetRecord):
        return dataset_entry
    if candidate is None:
        return dataset_entry

    note = "; ".join(
        filter(
            None,
            [
                dataset_record.migration_note,
                candidate.migration_note,
                "merged with the promoted candidate record",
            ],
        )
    )
    merged = DatasetRecord(
        dataset_id=dataset_id,
        table_id=dataset_record.table_id,
        publication_state=dataset_record.publication_state,
        owner=dataset_record.owner or candidate.owner,
        approval_state=dataset_record.approval_state or candidate.approval_state,
        migration_note=note or None,
        legacy_fields={**(candidate.legacy_fields or {}), **(dataset_record.legacy_fields or {})}
        or None,
    )
    return MigrationEntry(record_id=dataset_id, action=_ENTRY_IMPORT, record=merged)


def _section(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    section = payload.get(name)
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise LegacyOverlayError(f"Legacy overlay {name!r} section must be a JSON object.")
    return dict(section)


def _plan_config(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise LegacyOverlayError("Legacy overlay 'config' section must be a JSON object.")
    planned: dict[str, Any] = {}
    owner = config.get("default_owner")
    if owner is not None and not isinstance(owner, str):
        raise LegacyOverlayError("Legacy overlay config 'default_owner' must be a string.")
    planned["default_owner"] = owner
    approval_states = config.get("approval_states")
    if approval_states is None:
        planned["approval_states"] = []
    elif isinstance(approval_states, list) and all(
        isinstance(item, str) for item in approval_states
    ):
        planned["approval_states"] = [item for item in approval_states if item]
    else:
        raise LegacyOverlayError(
            "Legacy overlay config 'approval_states' must be a list of strings."
        )
    return planned


def _candidate_entries(candidates: Mapping[str, Any]) -> list[tuple[str, MigrationEntry]]:
    """Map every legacy candidate record to its import-or-reject entry."""
    results: list[tuple[str, MigrationEntry]] = []
    for table_id, record in candidates.items():
        if not isinstance(table_id, str) or not _valid_table_key(table_id):
            results.append(
                (
                    str(table_id),
                    MigrationEntry(
                        record_id=str(table_id),
                        action=_ENTRY_REJECT,
                        reason=f"Candidate key {table_id!r} is not a valid table key.",
                    ),
                )
            )
            continue
        entry_id = candidate_dataset_id(table_id)
        if not isinstance(record, Mapping):
            results.append(
                (
                    table_id,
                    MigrationEntry(
                        record_id=entry_id,
                        action=_ENTRY_REJECT,
                        reason="Candidate record must be a JSON object.",
                    ),
                )
            )
            continue
        results.append((table_id, _candidate_entry(table_id, dict(record))))
    return results


def _candidate_entry(table_id: str, record: dict[str, Any]) -> MigrationEntry:
    entry_id = candidate_dataset_id(table_id)
    state = record.get("state")
    try:
        workflow_state = WorkflowState.coerce(str(state)) if state is not None else None
    except ValueError:
        workflow_state = None
    if workflow_state is None:
        return MigrationEntry(
            record_id=entry_id,
            action=_ENTRY_REJECT,
            reason=f"Candidate {table_id!r} has no deterministic rule for state {state!r}.",
        )

    owner = record.get("owner")
    owner = owner if isinstance(owner, str) and owner else None
    approval_state = record.get("approval_state")
    approval_state = approval_state if isinstance(approval_state, str) and approval_state else None
    legacy_fields = _legacy_fields(record)

    if workflow_state is WorkflowState.PROMOTED:
        return _promoted_candidate_entry(
            table_id=table_id,
            record=record,
            owner=owner,
            approval_state=approval_state,
            legacy_fields=legacy_fields,
        )

    imported = CandidateRecord(
        dataset_id=entry_id,
        table_id=table_id,
        state=workflow_state.value,
        owner=owner,
        approval_state=approval_state,
        legacy_fields=legacy_fields,
    )
    return MigrationEntry(record_id=entry_id, action=_ENTRY_IMPORT, record=imported)


def _promoted_candidate_entry(
    *,
    table_id: str,
    record: dict[str, Any],
    owner: str | None,
    approval_state: str | None,
    legacy_fields: dict[str, Any] | None,
) -> MigrationEntry:
    entry_id = candidate_dataset_id(table_id)
    legacy_dataset_id = record.get("dataset_id")
    if legacy_dataset_id is not None and legacy_dataset_id != table_id:
        return MigrationEntry(
            record_id=entry_id,
            action=_ENTRY_REJECT,
            reason=(
                f"Promoted candidate {table_id!r} records dataset_id {legacy_dataset_id!r}; "
                "promotion must preserve the table key."
            ),
        )

    publication_state, note = _coerced_publication_state(record)
    imported = CandidateRecord(
        dataset_id=entry_id,
        table_id=table_id,
        state=WorkflowState.PROMOTED.value,
        owner=owner,
        approval_state=approval_state,
        promoted_dataset_id=table_id,
        publication_state=publication_state,
        migration_note=note,
        legacy_fields=legacy_fields,
    )
    return MigrationEntry(record_id=entry_id, action=_ENTRY_IMPORT, record=imported)


def _dataset_entries(datasets: Mapping[str, Any]) -> list[tuple[str, MigrationEntry]]:
    """Map every legacy datasets record keyed by its promoted ``<table_id>``."""
    results: list[tuple[str, MigrationEntry]] = []
    for dataset_id, record in datasets.items():
        if (
            not isinstance(dataset_id, str)
            or not _valid_table_key(dataset_id)
            or is_candidate_dataset_id(dataset_id)
        ):
            results.append(
                (
                    str(dataset_id),
                    MigrationEntry(
                        record_id=str(dataset_id),
                        action=_ENTRY_REJECT,
                        reason=f"Dataset key {dataset_id!r} is not a promoted <table_id> form.",
                    ),
                )
            )
            continue
        if not isinstance(record, Mapping):
            results.append(
                (
                    dataset_id,
                    MigrationEntry(
                        record_id=dataset_id,
                        action=_ENTRY_REJECT,
                        reason="Dataset record must be a JSON object.",
                    ),
                )
            )
            continue
        results.append((dataset_id, _dataset_entry(dataset_id, dict(record))))
    return results


def _dataset_entry(dataset_id: str, record: dict[str, Any]) -> MigrationEntry:
    table_id = dataset_table_id(dataset_id)
    publication_state, note = _coerced_publication_state(record)
    owner = record.get("owner")
    owner = owner if isinstance(owner, str) and owner else None
    approval_state = record.get("approval_state")
    approval_state = approval_state if isinstance(approval_state, str) and approval_state else None
    legacy_fields = _legacy_fields(record)
    imported = DatasetRecord(
        dataset_id=dataset_id,
        table_id=table_id,
        publication_state=publication_state,
        owner=owner,
        approval_state=approval_state,
        migration_note=note,
        legacy_fields=legacy_fields,
    )
    return MigrationEntry(record_id=dataset_id, action=_ENTRY_IMPORT, record=imported)


def _coerced_publication_state(record: Mapping[str, Any]) -> tuple[str, str | None]:
    """Return the record's publication state, mapping unknown/missing to draft.

    Unknown or missing publication states import as ``draft`` with a
    migration note; the original value is preserved in ``legacy_fields``.
    """
    raw = record.get("publication_state")
    if raw is None:
        return (
            PublicationState.DRAFT.value,
            "publication_state missing in legacy overlay; imported as draft",
        )
    value = str(raw)
    if value in _PUBLICATION_STATES:
        return value, None
    return PublicationState.DRAFT.value, (
        f"unknown legacy publication_state {value!r}; imported as draft"
    )


def _legacy_fields(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Capture legacy fields outside the durable record shapes, verbatim."""
    known = {
        "state",
        "owner",
        "approval_state",
        "dataset_id",
        "publication_state",
    }
    extra = {key: value for key, value in record.items() if key not in known}
    return extra or None


def _valid_table_key(value: str) -> bool:
    """Return whether ``value`` is a non-empty table key without ':' separators."""
    return bool(value) and value == value.strip() and ":" not in value


@runtime_checkable
class MigrationStore(Protocol):
    """Store contract for the transactional overlay apply and discard.

    Extends the neutral :class:`phlo.dataset.store.DatasetStateStore` with the
    migration transaction so the apply binds import, idempotency, and audit in
    one atomic write. Implementations are provider-owned; the explicit test
    mode ships its own in-memory double.
    """

    def commit_migration(
        self,
        *,
        records: tuple[DatasetStateRecord, ...],
        config: Mapping[str, Any],
        action_id: str,
        fingerprint: str,
        actor: str | None = None,
        scope: str | None = None,
    ) -> Any:
        """Commit every imported record plus the workflow config atomically.

        Replaying a committed ``action_id`` with the same fingerprint returns
        the stored result without duplicate state; a conflicting fingerprint or
        an existing record fails closed without writing.
        """
        ...

    def record_discard(
        self,
        *,
        source_digest: str,
        plan_digest: str,
        actor: str | None = None,
        scope: str | None = None,
    ) -> None:
        """Record the explicit discard of one planned overlay, audited and idempotent."""
        ...


__all__ = [
    "DISCARD_ACTION",
    "MIGRATION_ACTION",
    "OVERLAY_RESOURCE_ID",
    "LegacyOverlayError",
    "MigrationEntry",
    "MigrationPlan",
    "MigrationStore",
    "import_action_id",
    "plan_migration",
    "source_file_digest",
]
