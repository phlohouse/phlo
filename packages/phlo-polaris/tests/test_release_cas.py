"""Exercise release conflicts against real Iceberg metadata and local storage.

SQLite supplies the catalog and tmp_path supplies object storage; no external
service is required. Separate table handles expose optimistic-commit races.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock

import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog

from phlo_polaris.promotion import (
    IcebergReleaseStore,
    PolarisSnapshotPromotionCatalog,
    ReleaseConflictError,
    candidate_ref_for_run,
)


@pytest.fixture(autouse=True)
def publication_locks(monkeypatch):
    """Replace only PostgreSQL's lock driver with real thread synchronization."""
    locks = defaultdict(Lock)
    waiting = Event()

    class Database:
        def __enter__(self):
            self.lock = None
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def transactional_cursor(self):
            try:
                yield self
            finally:
                if self.lock is not None:
                    self.lock.release()

        def execute(self, statement, params):
            if not statement.startswith("SELECT pg_advisory_xact_lock("):
                raise ValueError(f"Unsupported lock statement: {statement}")
            self.lock = locks[params[0]]
            if not self.lock.acquire(blocking=False):
                waiting.set()
                self.lock.acquire()

    monkeypatch.setattr("phlo_postgres.resource.PostgresResource", Database)
    return waiting


@pytest.fixture
def catalog(tmp_path):
    catalog = SqlCatalog(
        "test", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=(tmp_path / "warehouse").as_uri()
    )
    catalog.create_namespace("bronze")
    table = catalog.create_table("bronze.events", schema=pa.schema([("event_id", pa.string())]))
    table.append(pa.Table.from_pylist([{"event_id": "seed"}]))
    return catalog


def _provider(catalog):
    return PolarisSnapshotPromotionCatalog(
        store=IcebergReleaseStore(catalog=catalog), table_opener=catalog.load_table
    )


def _stage(provider, run_id, event_id):
    provider.create_candidate(table_name="bronze.events", run_id=run_id)
    provider.merge_rows_into_candidate(
        table_name="bronze.events",
        run_id=run_id,
        rows=[{"event_id": event_id}],
        unique_key=["event_id"],
    )


def _state(release_id):
    return [{"kind": "state", "release_id": release_id, "revision": 1}]


@pytest.mark.parametrize("existing_snapshot", [False, True])
def test_only_one_ledger_writer_can_commit_the_version_it_read(catalog, existing_snapshot):
    store = IcebergReleaseStore(catalog=catalog)
    if existing_snapshot:
        store.append([{"kind": "candidate", "table_name": "bronze.events"}])
    _, first = store.read_for_update()
    _, second = store.read_for_update()
    store.append_if_unchanged(_state("winner"), first)

    with pytest.raises(ReleaseConflictError, match="changed during promotion"):
        store.append_if_unchanged(_state("loser"), second)

    assert [row["release_id"] for row in store.rows() if row["kind"] == "state"] == ["winner"]
    assert store.current_revision() == 1


def test_reopening_a_stale_candidate_cannot_erase_a_newer_release(catalog):
    provider = _provider(catalog)
    _stage(provider, "first", "a")
    _stage(provider, "second", "b")
    provider.promote_candidates(
        namespace="pipeline-run-first", release_id="first", expected_revision=0
    )

    reopened = provider.create_candidate(table_name="bronze.events", run_id="second")
    assert reopened.metadata["release_revision"] == 0
    with pytest.raises(ReleaseConflictError, match="stale or missing"):
        provider.promote_candidates(
            namespace="pipeline-run-second",
            release_id="second",
            expected_revision=provider.release_revision(),
        )

    assert set(
        catalog.load_table("bronze.events").scan().to_arrow().column("event_id").to_pylist()
    ) == {"seed", "a"}
    assert provider.resolve_release(table_name="bronze.events").release_id == "first"


@pytest.mark.parametrize("second_table", ["bronze.events", "bronze.orders"])
def test_concurrent_release_cannot_write_before_winning_publication_lock(
    catalog, publication_locks, second_table
):
    provider = _provider(catalog)
    _stage(provider, "first", "a")
    if second_table != "bronze.events":
        table = catalog.create_table(second_table, schema=pa.schema([("event_id", pa.string())]))
        table.append(pa.Table.from_pylist([{"event_id": "seed"}]))
    provider.create_candidate(table_name=second_table, run_id="second")
    provider.merge_rows_into_candidate(
        table_name=second_table, run_id="second", rows=[{"event_id": "b"}], unique_key=["event_id"]
    )
    inside_publication = Event()
    finish_first = Event()

    def wait_inside_publication(table_name):
        inside_publication.set()
        assert finish_first.wait(5), "first publisher was not released"
        return catalog.load_table(table_name)

    first = PolarisSnapshotPromotionCatalog(
        store=provider.store, table_opener=wait_inside_publication
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        winner = workers.submit(
            first.promote_candidates, namespace="pipeline-run-first", release_id="first"
        )
        try:
            assert inside_publication.wait(5), "first publisher did not enter the lock"
            loser = workers.submit(
                provider.promote_candidates, namespace="pipeline-run-second", release_id="second"
            )
            assert publication_locks.wait(5), "second publisher did not wait for the lock"
        finally:
            finish_first.set()
        assert winner.result(timeout=5)[0].release_id == "first"
        with pytest.raises(ReleaseConflictError, match="stale or missing"):
            loser.result(timeout=5)

    assert set(
        catalog.load_table("bronze.events").scan().to_arrow().column("event_id").to_pylist()
    ) == {"seed", "a"}
    assert (
        "b" not in catalog.load_table(second_table).scan().to_arrow().column("event_id").to_pylist()
    )
    assert provider.release_revision() == 1


def test_ledger_conflict_retains_candidate_and_retry_reuses_written_snapshot(catalog):
    provider = _provider(catalog)
    _stage(provider, "run", "a")
    table = catalog.load_table("bronze.events")
    overwrite = table.overwrite

    def overwrite_with_competing_ledger_append(*args, **kwargs):
        overwrite(*args, **kwargs)
        _, external_version = provider.store.read_for_update()
        provider.store.append_if_unchanged(
            [
                {
                    "kind": "candidate",
                    "table_name": "bronze.other",
                    "run_id": "other",
                    "snapshot_id": 123,
                }
            ],
            external_version,
        )

    table.overwrite = overwrite_with_competing_ledger_append
    contender = PolarisSnapshotPromotionCatalog(store=provider.store, table_opener=lambda _: table)
    with pytest.raises(ReleaseConflictError):
        contender.promote_candidates(namespace="pipeline-run-run", release_id="run")

    written_snapshot = catalog.load_table("bronze.events").current_snapshot().snapshot_id
    assert candidate_ref_for_run("run") in catalog.load_table("bronze.events").metadata.refs
    assert provider.resolve_release(table_name="bronze.events") is None
    result = provider.promote_candidates(namespace="pipeline-run-run", release_id="run")
    assert result[0].snapshot_id == written_snapshot
    assert provider.release_revision() == 1


def test_interrupted_publication_blocks_other_releases_until_resumed(catalog, monkeypatch):
    provider = _provider(catalog)
    _stage(provider, "first", "a")
    table = catalog.create_table("bronze.orders", schema=pa.schema([("event_id", pa.string())]))
    table.append(pa.Table.from_pylist([{"event_id": "seed"}]))
    provider.create_candidate(table_name="bronze.orders", run_id="second")
    provider.merge_rows_into_candidate(
        table_name="bronze.orders",
        run_id="second",
        rows=[{"event_id": "b"}],
        unique_key=["event_id"],
    )
    append = provider.store.append_if_unchanged

    def lose_receipt(rows, version):
        if any(row["kind"] == "release" for row in rows):
            raise RuntimeError("publication interrupted before receipt")
        append(rows, version)

    with monkeypatch.context() as patch:
        patch.setattr(provider.store, "append_if_unchanged", lose_receipt)
        with pytest.raises(RuntimeError, match="interrupted before receipt"):
            provider.promote_candidates(namespace="pipeline-run-first", release_id="first")

    restarted = _provider(catalog)
    assert restarted.abort_candidates(namespace="pipeline-run-first") is False
    with pytest.raises(ReleaseConflictError, match="interrupted promotion"):
        restarted.promote_candidates(namespace="pipeline-run-second", release_id="second")
    assert catalog.load_table("bronze.orders").scan().to_arrow().to_pylist() == [
        {"event_id": "seed"}
    ]
    written = catalog.load_table("bronze.events").current_snapshot().snapshot_id

    result = restarted.promote_candidates(namespace="pipeline-run-first", release_id="first")
    assert result[0].snapshot_id == written
    assert restarted.release_revision() == 1


def test_untracked_branch_cannot_be_rebased_onto_current_release(catalog):
    table = catalog.load_table("bronze.events")
    table.manage_snapshots().create_branch(
        table.current_snapshot().snapshot_id, candidate_ref_for_run("orphan")
    ).commit()
    with pytest.raises(ReleaseConflictError, match="no ledger baseline"):
        _provider(catalog).create_candidate(table_name="bronze.events", run_id="orphan")
