"""Unit tests for snapshot-based WAP contracts and helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from phlo.capabilities.interfaces import (
    CandidateSnapshot,
    CheckpointRecord,
    IngestionCheckpointStore,
    ReleaseRecord,
    SnapshotPromotionCatalog,
    SourceOffsetRange,
)
from phlo.config_schema import WapConfig
from phlo.exceptions import PhloConfigError
from phlo.helpers import wap as wap_module
from phlo.helpers.wap import (
    abort_candidates,
    candidate_namespace_for_run,
    ensure_candidate,
    promote_snapshots,
    resolve_snapshot_promotion_catalog,
    snapshot_write_audit_publish,
)


class FakePromotionCatalog(SnapshotPromotionCatalog):
    """In-memory SnapshotPromotionCatalog used to exercise helper behavior."""

    def __init__(self) -> None:
        self.candidates: dict[str, CandidateSnapshot] = {}
        self.aborted: list[str] = []
        self.releases: list[ReleaseRecord] = []
        self.revision = 0

    def create_candidate(self, *, table_name: str, run_id: str) -> CandidateSnapshot:
        candidate = CandidateSnapshot(
            table_name=table_name,
            snapshot_id=len(self.candidates) + 101,
            run_id=run_id,
            namespace=candidate_namespace_for_run(run_id),
            created_at=datetime.now(UTC),
        )
        self.candidates[table_name] = candidate
        return candidate

    def list_candidates(self, *, namespace: str) -> list[CandidateSnapshot]:
        return [c for c in self.candidates.values() if c.namespace == namespace]

    def promote_candidates(
        self,
        *,
        namespace: str,
        release_id: str,
        expected_revision: int | None = None,
        tables: list[str] | None = None,
    ) -> list[ReleaseRecord]:
        if expected_revision is not None and expected_revision != self.revision:
            raise PhloConfigError(message="release pointer moved")
        selected = [
            c
            for name, c in self.candidates.items()
            if c.namespace == namespace and (tables is None or name in tables)
        ]
        records: list[ReleaseRecord] = []
        for candidate in selected:
            self.revision += 1
            records.append(
                ReleaseRecord(
                    table_name=candidate.table_name,
                    snapshot_id=candidate.snapshot_id,
                    release_id=release_id,
                    revision=self.revision,
                    run_id=candidate.run_id,
                )
            )
        self.releases.extend(records)
        return records

    def resolve_release(self, *, table_name: str) -> ReleaseRecord | None:
        matches = [r for r in self.releases if r.table_name == table_name]
        return matches[-1] if matches else None

    def release_revision(self) -> int:
        return self.revision

    def abort_candidates(self, *, namespace: str) -> bool:
        self.aborted.append(namespace)
        return True

    def prune_candidates(self, *, older_than: datetime) -> list[str]:
        return []


class FakeCheckpointStore(IngestionCheckpointStore):
    """Minimal in-memory checkpoint store satisfying the protocol."""

    def __init__(self) -> None:
        self.records: dict[str, CheckpointRecord] = {}

    def claim(
        self,
        *,
        source_id: str,
        target_table: str,
        ranges: list[SourceOffsetRange],
        idempotency_key: str | None = None,
    ) -> CheckpointRecord:
        record = CheckpointRecord(
            checkpoint_id=f"cp-{len(self.records) + 1}",
            source_id=source_id,
            target_table=target_table,
            status="claimed",
            ranges=tuple(ranges),
            idempotency_key=idempotency_key,
        )
        self.records[record.checkpoint_id] = record
        return record

    def _replaced(self, record: CheckpointRecord, **updates: Any) -> CheckpointRecord:
        fields = {
            "checkpoint_id": record.checkpoint_id,
            "source_id": record.source_id,
            "target_table": record.target_table,
            "status": record.status,
            "ranges": record.ranges,
            "snapshot_id": record.snapshot_id,
            "release_id": record.release_id,
            "idempotency_key": record.idempotency_key,
            "failure_reason": record.failure_reason,
            "updated_at": record.updated_at,
        }
        fields.update(updates)
        replaced = CheckpointRecord(**fields)
        self.records[record.checkpoint_id] = replaced
        return replaced

    def record_snapshot(
        self,
        *,
        checkpoint_id: str,
        snapshot_id: int | str,
        release_id: str | None = None,
    ) -> CheckpointRecord:
        return self._replaced(
            self.records[checkpoint_id],
            status="staged",
            snapshot_id=snapshot_id,
            release_id=release_id,
        )

    def commit(self, *, checkpoint_id: str) -> CheckpointRecord:
        return self._replaced(self.records[checkpoint_id], status="committed")

    def fail(self, *, checkpoint_id: str, reason: str) -> CheckpointRecord:
        return self._replaced(self.records[checkpoint_id], status="failed", failure_reason=reason)

    def latest_committed(self, *, source_id: str, target_table: str) -> CheckpointRecord | None:
        committed = [
            r
            for r in self.records.values()
            if r.source_id == source_id
            and r.target_table == target_table
            and r.status == "committed"
        ]
        return committed[-1] if committed else None

    def find_by_idempotency_key(self, *, idempotency_key: str) -> CheckpointRecord | None:
        for record in self.records.values():
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def list_open(self, *, source_id: str) -> list[CheckpointRecord]:
        return [
            r
            for r in self.records.values()
            if r.source_id == source_id and r.status in {"claimed", "staged"}
        ]


def test_snapshot_promotion_and_checkpoint_protocols_are_runtime_checkable() -> None:
    catalog: Any = FakePromotionCatalog()
    store: Any = FakeCheckpointStore()
    assert isinstance(catalog, SnapshotPromotionCatalog)
    assert isinstance(store, IngestionCheckpointStore)


def test_candidate_namespace_is_deterministic_per_run() -> None:
    assert candidate_namespace_for_run("abc123") == "phlo_candidates__abc123"


def test_resolve_snapshot_promotion_catalog_rejects_branch_only_provider(monkeypatch) -> None:
    class BranchOnly:
        def list_branches(self) -> list[Any]:
            return []

    monkeypatch.setattr(
        wap_module,
        "resolve_capability",
        lambda *a, **k: SimpleNamespace(provider=BranchOnly()),
    )
    with pytest.raises(PhloConfigError, match="snapshot-based WAP promotion"):
        resolve_snapshot_promotion_catalog()


def test_ensure_candidate_creates_run_scoped_candidate() -> None:
    catalog = FakePromotionCatalog()
    candidate = ensure_candidate(table_name="bronze.events", run_id="run-1", catalog=catalog)
    assert candidate.table_name == "bronze.events"
    assert candidate.run_id == "run-1"
    assert candidate.namespace == "phlo_candidates__run-1"


def test_promote_snapshots_gated_on_all_checks_passing() -> None:
    catalog = FakePromotionCatalog()
    ensure_candidate(table_name="bronze.events", run_id="run-1", catalog=catalog)
    assert (
        promote_snapshots("phlo_candidates__run-1", [True, False], release_id="r1", catalog=catalog)
        == []
    )
    assert catalog.resolve_release(table_name="bronze.events") is None
    records = promote_snapshots(
        "phlo_candidates__run-1", [True, True], release_id="r2", catalog=catalog
    )
    assert len(records) == 1
    assert records[0].release_id == "r2"
    assert records[0].snapshot_id == 101


def test_snapshot_write_audit_publish_promotes_on_success_and_keeps_failures_auditable() -> None:
    catalog = FakePromotionCatalog()
    with snapshot_write_audit_publish(run_id="run-1", tables=["bronze.events"], catalog=catalog):
        assert catalog.resolve_release(table_name="bronze.events") is None
    assert catalog.resolve_release(table_name="bronze.events") is not None

    failed_catalog = FakePromotionCatalog()
    with (
        pytest.raises(RuntimeError),
        snapshot_write_audit_publish(
            run_id="run-2", tables=["bronze.events"], catalog=failed_catalog
        ),
    ):
        raise RuntimeError("quality gate exploded")
    # Failed candidates stay discoverable for audit but are never released.
    assert failed_catalog.resolve_release(table_name="bronze.events") is None
    assert len(failed_catalog.list_candidates(namespace="phlo_candidates__run-2")) == 1


def test_abort_candidates_delegates_to_catalog() -> None:
    catalog = FakePromotionCatalog()
    assert abort_candidates("phlo_candidates__run-1", catalog=catalog) is True
    assert catalog.aborted == ["phlo_candidates__run-1"]


def test_wap_config_strategy_defaults_to_branch_and_validates() -> None:
    assert WapConfig().strategy == "branch"
    assert WapConfig(strategy="snapshot").strategy == "snapshot"
    with pytest.raises(ValueError):
        WapConfig(strategy="merge")
