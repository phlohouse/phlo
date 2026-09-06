"""Tests for the Polaris snapshot promotion state machine."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from phlo_polaris.promotion import (
    CANDIDATE_REF_PREFIX,
    PolarisSnapshotPromotionCatalog,
    ReleaseConflictError,
    candidate_ref_for_run,
    run_id_from_namespace,
)


class FakeStore:
    """In-memory ledger standing in for the Iceberg release store."""

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self.appended: list[list[dict]] = []

    def rows(self) -> list[dict]:
        return list(self._rows)

    def read_for_update(self):
        return self.rows(), len(self._rows)

    def publication_lock(self):
        return nullcontext()

    def append_if_unchanged(self, rows, version):
        if version != len(self._rows):
            raise ReleaseConflictError(message="Release ledger changed during promotion.")
        self.append(rows)

    def append(self, rows: list[dict]) -> None:
        self.appended.append(rows)
        self._rows.extend(rows)

    def current_revision(self) -> int:
        return max(
            (int(r["revision"]) for r in self._rows if r.get("kind") == "state"),
            default=0,
        )


class FakeArrow:
    """Minimal arrow stand-in carrying rows and typed columns."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def column(self, name: str):
        return SimpleNamespace(to_pylist=lambda name=name: [row.get(name) for row in self.rows])

    def to_pylist(self) -> list[dict]:
        return list(self.rows)


class FakeScan:
    def __init__(self, rows: list[dict], columns: list[str] | None = None) -> None:
        self._rows = rows
        self._columns = columns

    def select(self, *columns: str) -> "FakeScan":
        return FakeScan(self._rows, list(columns))

    def to_arrow(self) -> FakeArrow:
        if self._columns is None:
            return FakeArrow(self._rows)
        return FakeArrow([{name: row.get(name) for name in self._columns} for row in self._rows])


class FakeSnapshotManager:
    def __init__(self, table: "FakeTable") -> None:
        self._table = table
        self._operations: list[tuple] = []

    def create_branch(self, snapshot_id: int, branch_name: str) -> "FakeSnapshotManager":
        self._operations.append(("create", branch_name, snapshot_id))
        return self

    def remove_branch(self, ref: str) -> "FakeSnapshotManager":
        self._operations.append(("drop", ref))
        return self

    def commit(self) -> None:
        for operation in self._operations:
            if operation[0] == "create":
                self._table.refs[operation[1]] = SimpleNamespace(snapshot_id=operation[2])
            else:
                self._table.refs.pop(operation[1], None)
                self._table.dropped.append(operation[1])
        self._operations = []


class FakeTable:
    """Fake Iceberg table with branch rows, overwrite, and ref management."""

    def __init__(self, rows: list[dict] | None = None, snapshot_id: int = 11) -> None:
        self.main_rows: list[dict] = list(rows or [])
        self.snapshot_id = snapshot_id
        self.next_snapshot_id = snapshot_id
        self.snapshot_properties: dict[str, str] = {}
        self.refs: dict[str, object] = {}
        self.branch_rows: dict[str, list[dict]] = {}
        self.dropped: list[str] = []
        self.overwrites: list[list[dict]] = []

    @property
    def metadata(self) -> SimpleNamespace:
        return SimpleNamespace(refs=self.refs)

    def current_snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(
            snapshot_id=self.snapshot_id,
            summary=SimpleNamespace(additional_properties=self.snapshot_properties),
        )

    def scan(self, *, snapshot_id: int | None = None) -> FakeScan:
        if snapshot_id is None:
            return FakeScan(self.main_rows)
        for ref, ref_ns in self.refs.items():
            if ref_ns.snapshot_id == snapshot_id:
                return FakeScan(self.branch_rows.get(ref, self.main_rows))
        return FakeScan(self.main_rows)

    def append(self, arrow: FakeArrow, *, branch: str | None = None) -> None:
        if hasattr(arrow, "to_pylist"):
            arrow = FakeArrow(arrow.to_pylist())
        if branch:
            new_rows = list(self.branch_rows.get(branch, self.main_rows)) + list(arrow.rows)
            self.branch_rows[branch] = new_rows
            self.next_snapshot_id += 1
            self.refs[branch] = SimpleNamespace(snapshot_id=self.next_snapshot_id)
        else:
            self.main_rows = self.main_rows + list(arrow.rows)
            self.next_snapshot_id += 1
            self.snapshot_id = self.next_snapshot_id

    def overwrite(self, arrow: FakeArrow, **kwargs) -> None:
        if hasattr(arrow, "to_pylist"):
            arrow = FakeArrow(arrow.to_pylist())
        self.overwrites.append(list(arrow.rows))
        self.main_rows = list(arrow.rows)
        self.next_snapshot_id += 1
        self.snapshot_id = self.next_snapshot_id
        self.snapshot_properties = kwargs.get("snapshot_properties", {})

    def manage_snapshots(self) -> FakeSnapshotManager:
        return FakeSnapshotManager(self)


def _catalog() -> tuple[PolarisSnapshotPromotionCatalog, FakeStore, dict]:
    store = FakeStore()
    tables = {
        "bronze.events": FakeTable([{"event_id": "seed"}], snapshot_id=11),
        "bronze.orders": FakeTable([], snapshot_id=22),
    }
    catalog = PolarisSnapshotPromotionCatalog(store=store, table_opener=lambda name: tables[name])
    return catalog, store, tables


def test_candidate_ref_is_deterministic_and_reversible() -> None:
    assert candidate_ref_for_run("abc") == f"{CANDIDATE_REF_PREFIX}abc"
    assert run_id_from_namespace("pipeline-run-abc") == "abc"
    assert run_id_from_namespace("phlo_candidates__abc") == "abc"


def test_create_candidate_opens_branch_and_records_ledger_row() -> None:
    catalog, store, tables = _catalog()
    candidate = catalog.create_candidate(table_name="bronze.events", run_id="run-1")
    assert candidate.snapshot_id == 11
    assert candidate.namespace == "pipeline-run-run-1"
    assert CANDIDATE_REF_PREFIX + "run-1" in tables["bronze.events"].refs
    assert store.rows()[0]["kind"] == "candidate"
    assert store.rows()[0]["status"] == "open"


def test_merge_rows_into_candidate_dedups_against_branch_history() -> None:
    catalog, _, tables = _catalog()
    catalog.create_candidate(table_name="bronze.events", run_id="run-1")
    ref = CANDIDATE_REF_PREFIX + "run-1"

    first = catalog.merge_rows_into_candidate(
        table_name="bronze.events",
        run_id="run-1",
        rows=[{"event_id": "e1"}, {"event_id": "e2"}, {"event_id": "e2"}],
        unique_key=["event_id"],
    )
    assert first["appended"] == 2
    assert first["duplicates_dropped"] == 1

    replay = catalog.merge_rows_into_candidate(
        table_name="bronze.events",
        run_id="run-1",
        rows=[{"event_id": "e1"}, {"event_id": "e3"}],
        unique_key=["event_id"],
    )
    assert replay["appended"] == 1
    assert replay["duplicates_dropped"] == 1

    table = tables["bronze.events"]
    assert [row["event_id"] for row in table.branch_rows[ref]] == ["seed", "e1", "e2", "e3"]
    # Main still hides the candidate rows before promotion.
    assert [row["event_id"] for row in table.main_rows] == ["seed"]


def test_merge_without_open_candidate_fails_closed() -> None:
    catalog, _, _ = _catalog()
    with pytest.raises(Exception, match="not open"):
        catalog.merge_rows_into_candidate(
            table_name="bronze.events",
            run_id="ghost",
            rows=[{"event_id": "e1"}],
            unique_key=["event_id"],
        )


def test_promote_overwrites_main_and_advances_pointer() -> None:
    catalog, store, tables = _catalog()
    catalog.create_candidate(table_name="bronze.events", run_id="run-1")
    catalog.merge_rows_into_candidate(
        table_name="bronze.events",
        run_id="run-1",
        rows=[{"event_id": "e1"}],
        unique_key=["event_id"],
    )
    catalog.create_candidate(table_name="bronze.orders", run_id="run-1")

    records = catalog.promote_candidates(
        namespace="pipeline-run-run-1", release_id="release-1", expected_revision=0
    )

    assert {record.table_name for record in records} == {"bronze.events", "bronze.orders"}
    assert all(record.revision == 1 for record in records)
    events = tables["bronze.events"]
    # Candidate content became main content atomically; branch dropped.
    assert [row["event_id"] for row in events.main_rows] == ["seed", "e1"]
    assert events.refs == {}
    # One atomic append carries the release rows and the new pointer state.
    assert len(store.appended[-1]) == 3
    assert catalog.release_revision() == 1


def test_promote_refuses_stale_expected_revision() -> None:
    catalog, _, _ = _catalog()
    catalog.create_candidate(table_name="bronze.events", run_id="run-1")
    catalog.promote_candidates(namespace="pipeline-run-run-1", release_id="r1")

    with pytest.raises(ReleaseConflictError, match="Release pointer moved"):
        catalog.promote_candidates(
            namespace="pipeline-run-run-1",
            release_id="r2",
            expected_revision=0,
        )


def test_resolve_release_returns_latest_revision_record() -> None:
    catalog, _, _ = _catalog()
    catalog.create_candidate(table_name="bronze.events", run_id="run-1")
    catalog.promote_candidates(namespace="pipeline-run-run-1", release_id="r1")
    catalog.create_candidate(table_name="bronze.events", run_id="run-2")
    catalog.promote_candidates(namespace="pipeline-run-run-2", release_id="r2")

    release = catalog.resolve_release(table_name="bronze.events")
    assert release is not None
    assert release.release_id == "r2"
    assert release.revision == 2


def test_promote_without_candidates_returns_empty() -> None:
    catalog, _, _ = _catalog()
    assert catalog.promote_candidates(namespace="pipeline-run-none", release_id="r1") == []


def test_abort_candidates_drops_refs_and_marks_ledger() -> None:
    catalog, store, tables = _catalog()
    catalog.create_candidate(table_name="bronze.events", run_id="run-1")

    assert catalog.abort_candidates(namespace="pipeline-run-run-1") is True
    assert tables["bronze.events"].refs == {}
    statuses = [row["status"] for row in store.rows() if row["kind"] == "candidate"]
    assert statuses.count("aborted") == 1
    assert catalog.list_candidates(namespace="pipeline-run-run-1") == []


def test_abort_is_idempotent_when_nothing_is_open() -> None:
    catalog, _, _ = _catalog()
    assert catalog.abort_candidates(namespace="pipeline-run-empty") is True


def test_prune_candidates_drops_only_expired_refs() -> None:
    catalog, store, tables = _catalog()
    catalog.create_candidate(table_name="bronze.events", run_id="old")
    for row in store.rows():
        if row.get("run_id") == "old":
            row["recorded_at"] = datetime.now(timezone.utc) - timedelta(hours=48)
    catalog.create_candidate(table_name="bronze.orders", run_id="new")

    pruned = catalog.prune_candidates(older_than=datetime.now(timezone.utc) - timedelta(hours=24))

    assert pruned == ["pipeline-run-old"]
    assert tables["bronze.events"].refs == {}
    assert tables["bronze.orders"].refs != {}
