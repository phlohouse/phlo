"""Migration planner, apply, discard, and idempotency tests.

The canonical legacy fixture is one Observatory overlay holding claimed,
promoted, rejected, published, and draft workflow state plus its config. The
planner is read-only; apply runs against the explicit memory-mode store, which
implements the same transactional contract as the provider-owned durable
store.
"""

from __future__ import annotations

import json

import pytest

from phlo.dataset import (
    CandidateRecord,
    DatasetRecord,
    LegacyOverlayError,
    MigrationStore,
    StoreWrite,
    WorkflowState,
    import_action_id,
    plan_migration,
)
from phlo.dataset.models import PublicationState
from phlo.dataset.store import StoreWriteStatus
from phlo.dataset_state import MemoryDatasetStateStore

LEGACY_OVERLAY: dict = {
    "datasets": {
        "gold.customer_health": {
            "publication_state": "published",
            "approval_state": "approved",
            "owner": "alice",
        },
        "gold.lead_scores": {"publication_state": "retired", "approval_state": "retired"},
        "gold.untouched": {},
    },
    "candidates": {
        "gold.customer_health": {
            "state": "promoted",
            "owner": "alice",
            "dataset_id": "gold.customer_health",
            "publication_state": "draft",
        },
        "gold.lead_scores": {"state": "review", "owner": "bob", "approval_state": "review"},
        "gold.abandoned": {"state": "rejected"},
        "gold.open": {"state": "claimed", "owner": "carol"},
    },
    "config": {"default_owner": "ops", "approval_states": ["draft", "review", "approved"]},
}


@pytest.fixture()
def store() -> MemoryDatasetStateStore:
    return MemoryDatasetStateStore()


def test_plan_is_deterministic_and_preserves_ids() -> None:
    first = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    second = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    assert first.to_read_model() == second.to_read_model()
    assert first.plan_digest() == second.plan_digest()

    imported_ids = [entry.record_id for entry in first.imports]
    assert imported_ids == [
        "candidate:gold.abandoned",
        "candidate:gold.customer_health",
        "candidate:gold.lead_scores",
        "candidate:gold.open",
        "gold.customer_health",
        "gold.lead_scores",
        "gold.untouched",
    ]


def test_plan_promotes_candidate_identity_and_publication_state() -> None:
    plan = plan_migration(LEGACY_OVERLAY)
    candidate = next(
        e.record for e in plan.imports if e.record_id == "candidate:gold.customer_health"
    )
    assert isinstance(candidate, CandidateRecord)
    assert candidate.state == WorkflowState.PROMOTED.value
    assert candidate.promoted_dataset_id == "gold.customer_health"
    assert candidate.publication_state == PublicationState.DRAFT.value
    assert candidate.owner == "alice"

    promoted = next(e.record for e in plan.imports if e.record_id == "gold.customer_health")
    assert isinstance(promoted, DatasetRecord)
    assert promoted.publication_state == PublicationState.PUBLISHED.value
    assert promoted.owner == "alice"


def test_plan_preserves_terminal_and_unknown_states() -> None:
    plan = plan_migration(LEGACY_OVERLAY)
    rejected = next(e.record for e in plan.imports if e.record_id == "candidate:gold.abandoned")
    assert isinstance(rejected, CandidateRecord)
    assert rejected.state == WorkflowState.REJECTED.value

    retired = next(e.record for e in plan.imports if e.record_id == "gold.lead_scores")
    assert isinstance(retired, DatasetRecord)
    assert retired.publication_state == PublicationState.RETIRED.value


def test_plan_maps_unknown_publication_state_to_draft_with_note() -> None:
    plan = plan_migration({"datasets": {"gold.x": {"publication_state": "mysterious"}}})
    entry = plan.imports[0]
    record = entry.record
    assert isinstance(record, DatasetRecord)
    assert record.publication_state == PublicationState.DRAFT.value
    assert record.migration_note is not None
    assert "mysterious" in record.migration_note


def test_plan_rejects_records_without_deterministic_rules() -> None:
    plan = plan_migration(
        {
            "candidates": {"gold.ghost": {"state": "teleported"}, "": {}},
            "datasets": {"candidate:gold.ghost": {"publication_state": "draft"}},
        }
    )
    rejected = {entry.record_id for entry in plan.rejections}
    assert rejected == {"candidate:gold.ghost", ""}
    assert all(
        "deterministic" in entry.reason or "valid" in entry.reason for entry in plan.rejections
    )


def test_plan_fails_closed_on_ahead_schema_version() -> None:
    with pytest.raises(LegacyOverlayError, match="ahead"):
        plan_migration({"schema_version": 3, "datasets": {}, "candidates": {}})


def test_plan_fails_closed_on_malformed_payload() -> None:
    with pytest.raises(LegacyOverlayError):
        plan_migration(["not", "an", "object"])  # type: ignore[list-item]
    with pytest.raises(LegacyOverlayError):
        plan_migration({"datasets": "nope"})
    with pytest.raises(LegacyOverlayError):
        plan_migration({"config": "nope"})
    with pytest.raises(LegacyOverlayError):
        plan_migration({"config": {"approval_states": "nope"}})


def test_plan_is_read_only_over_the_source(store: MemoryDatasetStateStore) -> None:
    payload = json.loads(json.dumps(LEGACY_OVERLAY))
    plan_migration(payload)
    assert payload == LEGACY_OVERLAY
    assert not store.audit_events()
    assert store.workflow_config() == {}


def test_apply_imports_once_with_preserved_ids(store: MemoryDatasetStateStore) -> None:
    plan = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    result = store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=import_action_id("digest-1"),
        fingerprint=plan.plan_digest(),
        actor="operator-alice",
        scope="lakehouse:operate",
    )
    assert result.status is StoreWriteStatus.COMMITTED
    assert {record.dataset_id for record in result.records} == {
        entry.record_id for entry in plan.imports
    }
    assert store.workflow_config() == LEGACY_OVERLAY["config"]
    loaded = store.load("gold.customer_health")
    assert isinstance(loaded, DatasetRecord)
    assert loaded.publication_state == PublicationState.PUBLISHED.value
    assert loaded.owner == "alice"
    assert loaded.last_action_id is None  # migration is not a workflow transition
    statuses = [event.outcome for event in store.audit_events()]
    assert statuses == ["committed"]


def test_apply_replays_stored_result_without_duplicate_state(
    store: MemoryDatasetStateStore,
) -> None:
    plan = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    action_id = import_action_id("digest-1")
    first = store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=action_id,
        fingerprint=plan.plan_digest(),
        actor="operator-alice",
    )
    second = store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=action_id,
        fingerprint=plan.plan_digest(),
        actor="operator-alice",
    )
    assert first.status is StoreWriteStatus.COMMITTED
    assert second.status is StoreWriteStatus.REPLAYED
    assert [r.dataset_id for r in second.records] == [r.dataset_id for r in first.records]
    assert len(store.audit_events()) == 1


def test_apply_fails_closed_on_conflicting_plan(store: MemoryDatasetStateStore) -> None:
    plan = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    action_id = import_action_id("digest-1")
    store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=action_id,
        fingerprint=plan.plan_digest(),
    )
    other = plan_migration(LEGACY_OVERLAY, source_digest="digest-2")
    conflict = store.commit_migration(
        records=other.records,
        config=other.config,
        action_id=action_id,
        fingerprint=other.plan_digest(),
    )
    assert conflict.status is StoreWriteStatus.ACTION_CONFLICT
    assert len(store.audit_events()) == 1


def test_apply_fails_closed_when_records_already_exist(store: MemoryDatasetStateStore) -> None:
    store.commit_migration(
        records=(DatasetRecord(dataset_id="gold.x", table_id="gold.x", publication_state="draft"),),
        config={},
        action_id="other-import",
        fingerprint="fp",
    )
    plan = plan_migration({"datasets": {"gold.x": {}}}, source_digest="digest-1")
    result = store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=import_action_id("digest-1"),
        fingerprint=plan.plan_digest(),
    )
    assert result.status is StoreWriteStatus.ACTION_CONFLICT
    assert "gold.x" in result.detail


def test_discard_is_audited_and_idempotent(store: MemoryDatasetStateStore) -> None:
    plan = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    store.record_discard(
        source_digest=plan.source_digest, plan_digest=plan.plan_digest(), actor="operator-bob"
    )
    store.record_discard(
        source_digest=plan.source_digest, plan_digest=plan.plan_digest(), actor="operator-bob"
    )
    events = [event for event in store.audit_events() if event.action == "discard-overlay"]
    assert len(events) == 1
    assert events[0].actor == "operator-bob"
    assert events[0].outcome == "discarded"
    assert store.load("gold.customer_health") is None


def test_migrated_records_participate_in_normal_transitions(
    store: MemoryDatasetStateStore,
) -> None:
    plan = plan_migration(LEGACY_OVERLAY, source_digest="digest-1")
    store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=import_action_id("digest-1"),
        fingerprint=plan.plan_digest(),
    )
    # The imported promoted dataset can retire like any promoted dataset.
    result = store.compare_and_set(
        writes=(
            StoreWrite(
                record_id="gold.customer_health",
                expected_state=PublicationState.PUBLISHED.value,
                next_record=DatasetRecord(
                    dataset_id="gold.customer_health",
                    table_id="gold.customer_health",
                    publication_state=PublicationState.RETIRED.value,
                    owner="alice",
                    approval_state="retired",
                ),
            ),
        ),
        action_id="retire-1",
        action="retire",
        fingerprint="retire-1-fp",
    )
    assert result.status is StoreWriteStatus.COMMITTED


def test_memory_store_satisfies_the_migration_contract() -> None:
    assert isinstance(MemoryDatasetStateStore(), MigrationStore)
