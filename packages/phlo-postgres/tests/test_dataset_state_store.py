"""Tests for the provider-owned durable Dataset state store.

The store under test is ``phlo_postgres.dataset_state_store
.SettingsDatasetStateStore``: the transactional, project-scoped
settings-backed store. Tests run it over a file-locked
settings double so the two-process proof drives real operating-system
processes through the same serialized ``mutate`` transaction the PostgreSQL
backend provides with advisory row locks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from phlo.dataset import (
    CandidateRecord,
    DatasetRecord,
    state_store_namespace,
)
from phlo.dataset.models import PublicationState, WorkflowState
from phlo.dataset.store import StoreWrite, StoreWriteStatus
from phlo.plugins.observatory_settings import SettingsScope, StorageCorruptionError
from phlo_postgres.dataset_state_store import (
    DatasetStateStoreProvider,
    SettingsDatasetStateStore,
    get_dataset_state_stores,
)
from phlo_postgres.plugin import PostgresResourceProvider

from _dataset_test_backends import FileLockSettingsStore

WORKER = Path(__file__).parent / "_dataset_worker.py"


def make_store(path: Path, project_root: str = "/projects/demo") -> SettingsDatasetStateStore:
    return SettingsDatasetStateStore(
        settings_store=FileLockSettingsStore(path),
        namespace=state_store_namespace(project_root),
    )


def draft_record(dataset_id: str = "gold.demo") -> DatasetRecord:
    return DatasetRecord(dataset_id=dataset_id, table_id=dataset_id, publication_state="draft")


# ---------------------------------------------------------------------------
# Capability registration
# ---------------------------------------------------------------------------


def test_get_dataset_state_stores_returns_capability_spec() -> None:
    specs = get_dataset_state_stores()
    assert len(specs) == 1
    assert specs[0].name == "postgres"
    assert isinstance(specs[0].provider, DatasetStateStoreProvider)


def test_resource_provider_exposes_dataset_state_store() -> None:
    specs = PostgresResourceProvider().get_dataset_state_stores()
    assert len(specs) == 1
    assert isinstance(specs[0].provider, DatasetStateStoreProvider)


# ---------------------------------------------------------------------------
# Compare-and-set semantics
# ---------------------------------------------------------------------------


def test_compare_and_set_commits_and_replays(tmp_path: Path) -> None:
    store = make_store(tmp_path / "state.json")
    write = StoreWrite(record_id="gold.demo", expected_state="open", next_record=draft_record())
    first = store.compare_and_set(
        writes=(write,), action_id="a1", action="promote", fingerprint="fp-1"
    )
    assert first.status is StoreWriteStatus.COMMITTED

    replay = store.compare_and_set(
        writes=(write,), action_id="a1", action="promote", fingerprint="fp-1"
    )
    assert replay.status is StoreWriteStatus.REPLAYED
    assert replay.committed_fingerprint == "fp-1"

    conflict = store.compare_and_set(
        writes=(write,), action_id="a1", action="promote", fingerprint="fp-2"
    )
    assert conflict.status is StoreWriteStatus.ACTION_CONFLICT

    assert store.committed_action("gold.demo", "a1").fingerprint == "fp-1"
    assert store.committed_action("gold.demo", "a2") is None


def test_compare_and_set_fails_on_precondition_mismatch(tmp_path: Path) -> None:
    store = make_store(tmp_path / "state.json")
    stale = StoreWrite(record_id="gold.demo", expected_state="open", next_record=draft_record())
    store.compare_and_set(writes=(stale,), action_id="a1", action="promote", fingerprint="fp")
    moved = StoreWrite(
        record_id="gold.demo",
        expected_state="open",
        next_record=DatasetRecord(
            dataset_id="gold.demo", table_id="gold.demo", publication_state="published"
        ),
    )
    result = store.compare_and_set(
        writes=(moved,), action_id="a2", action="publish", fingerprint="fp2"
    )
    assert result.status is StoreWriteStatus.PRECONDITION_FAILED


def test_promote_writes_both_records_atomically(tmp_path: Path) -> None:
    store = make_store(tmp_path / "state.json")
    table_id = "gold.demo"
    # Seed the candidate in review, the promote pre-state.
    seed = CandidateRecord(dataset_id=f"candidate:{table_id}", table_id=table_id, state="review")
    store.compare_and_set(
        writes=(
            StoreWrite(record_id=f"candidate:{table_id}", expected_state="open", next_record=seed),
        ),
        action_id="review-1",
        action="review",
        fingerprint="review-fp",
    )
    writes = (
        StoreWrite(
            record_id=f"candidate:{table_id}",
            expected_state="review",
            next_record=CandidateRecord(
                dataset_id=f"candidate:{table_id}",
                table_id=table_id,
                state=WorkflowState.PROMOTED.value,
                promoted_dataset_id=table_id,
                publication_state="draft",
            ),
        ),
        StoreWrite(
            record_id=table_id,
            expected_state="open",
            next_record=DatasetRecord(
                dataset_id=table_id, table_id=table_id, publication_state="draft"
            ),
        ),
    )
    result = store.compare_and_set(
        writes=writes, action_id="p1", action="promote", fingerprint="fp"
    )
    assert result.status is StoreWriteStatus.COMMITTED
    assert len(result.records) == 2
    assert store.load(f"candidate:{table_id}").state == WorkflowState.PROMOTED.value
    assert store.load(table_id).current_state == "draft"


def test_append_audit_and_audit_events_are_durable(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = make_store(path)
    from phlo.dataset import TransitionAuditEvent

    store.append_audit(
        TransitionAuditEvent(
            actor="alice",
            scope="lakehouse:operate",
            action_id="a1",
            resource_id="gold.demo",
            action="publish",
            before_state="draft",
            after_state="published",
            outcome="committed",
        )
    )
    # A fresh instance over the same backend observes the same audit stream.
    reloaded = make_store(path)
    assert len(reloaded.audit_events()) == 1
    assert reloaded.audit_events()[0].actor == "alice"


def test_corrupt_payload_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    backend = FileLockSettingsStore(path)
    namespace = state_store_namespace("/projects/demo")
    backend.put(SettingsScope.GLOBAL, namespace, {"schema_version": 99, "records": {}})
    store = SettingsDatasetStateStore(settings_store=backend, namespace=namespace)
    with pytest.raises(StorageCorruptionError):
        store.load("gold.demo")


# ---------------------------------------------------------------------------
# Migration transaction
# ---------------------------------------------------------------------------


def test_commit_migration_is_atomic_idempotent_and_audited(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = make_store(path)
    records = (
        CandidateRecord(dataset_id="candidate:gold.x", table_id="gold.x", state="claimed"),
        DatasetRecord(dataset_id="gold.x", table_id="gold.x", publication_state="draft"),
    )
    first = store.commit_migration(
        records=records,
        config={"default_owner": "ops"},
        action_id="overlay-import-d1",
        fingerprint="plan-fp",
        actor="operator-alice",
        scope="lakehouse:operate",
    )
    assert first.status is StoreWriteStatus.COMMITTED
    assert store.workflow_config() == {"default_owner": "ops"}

    replay = store.commit_migration(
        records=records,
        config={"default_owner": "ops"},
        action_id="overlay-import-d1",
        fingerprint="plan-fp",
        actor="operator-alice",
    )
    assert replay.status is StoreWriteStatus.REPLAYED
    assert [r.dataset_id for r in replay.records] == [r.dataset_id for r in first.records]

    conflict = store.commit_migration(
        records=records,
        config={},
        action_id="overlay-import-d1",
        fingerprint="other-plan",
    )
    assert conflict.status is StoreWriteStatus.ACTION_CONFLICT

    events = store.audit_events()
    assert len(events) == 1
    assert events[0].action == "migrate-overlay"
    assert events[0].actor == "operator-alice"


def test_record_discard_is_audited_once(tmp_path: Path) -> None:
    store = make_store(tmp_path / "state.json")
    store.record_discard(source_digest="d1", plan_digest="p1", actor="operator-bob")
    store.record_discard(source_digest="d1", plan_digest="p1", actor="operator-bob")
    discards = [event for event in store.audit_events() if event.action == "discard-overlay"]
    assert len(discards) == 1
    assert discards[0].outcome == "discarded"


# ---------------------------------------------------------------------------
# Restart persistence
# ---------------------------------------------------------------------------


def test_state_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = make_store(path)
    store.compare_and_set(
        writes=(
            StoreWrite(record_id="gold.demo", expected_state="open", next_record=draft_record()),
        ),
        action_id="a1",
        action="promote",
        fingerprint="fp",
    )
    reloaded = make_store(path)
    record = reloaded.load("gold.demo")
    assert record is not None
    assert record.current_state == "draft"
    assert reloaded.committed_action("gold.demo", "a1") is not None


# ---------------------------------------------------------------------------
# Two-process atomicity
# ---------------------------------------------------------------------------


def _run_worker(state_file: Path, project_root: str, action_id: str, expected: str) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--state-file",
            str(state_file),
            "--project-root",
            project_root,
            "--action-id",
            action_id,
            "--expected-state",
            expected,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_two_processes_race_one_publication_transition(tmp_path: Path) -> None:
    """Two workers race the same publish: one commit, one replay, one truth."""
    state_file = tmp_path / "shared-state.json"
    project_root = str(tmp_path / "project")

    # Seed the draft dataset the way a first worker's promote would.
    seed = make_store(state_file, project_root)
    seed.compare_and_set(
        writes=(
            StoreWrite(record_id="gold.demo", expected_state="open", next_record=draft_record()),
        ),
        action_id="promote-1",
        action="promote",
        fingerprint="promote-fp",
    )

    workers = [
        subprocess.Popen(
            [
                sys.executable,
                str(WORKER),
                "--state-file",
                str(state_file),
                "--project-root",
                project_root,
                "--action-id",
                "publish-1",
                "--expected-state",
                "",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=60)
        assert worker.returncode == 0, stderr
        outputs.append(json.loads(stdout.strip().splitlines()[-1]))

    statuses = sorted(outcome["status"] for outcome in outputs)
    assert statuses == ["committed", "replayed"], outputs
    assert {outcome["observed_state"] for outcome in outputs} == {"published"}

    # One canonical state after restart: a fresh store instance reads the
    # durable record, not any worker's memory.
    final = make_store(state_file, project_root)
    record = final.load("gold.demo")
    assert record.current_state == PublicationState.PUBLISHED.value
    events = final.audit_events()
    outcomes = sorted(event.outcome for event in events if event.action == "publish")
    assert outcomes == ["committed", "replayed"]
