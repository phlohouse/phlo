"""Tests for the PostgreSQL-backed ingestion checkpoint store.

Verifies the claim → staged → committed lifecycle, fail-closed transitions,
and idempotent claim resumption against a fake PostgresResource.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest
from phlo.capabilities.interfaces import IngestionCheckpointStore, SourceOffsetRange
from phlo_postgres.checkpoints import PostgresIngestionCheckpointStore
from phlo_postgres.plugin import PostgresResourceProvider


class FakeCursor:
    """Scripted psycopg2 cursor returning queued rows per executed statement."""

    def __init__(self, results: list[tuple | None]) -> None:
        self._results = list(results)
        self.statements: list[str] = []
        self.params: list[tuple | None] = []

    def execute(self, statement: str, params: tuple | None = None) -> None:
        self.statements.append(" ".join(statement.split()))
        self.params.append(params)

    def fetchone(self) -> tuple | None:
        if not self._results:
            return None
        item = self._results.pop(0)
        return item

    def fetchall(self) -> list[tuple]:
        rows = list(self._results)
        self._results.clear()
        return rows or []

    def close(self) -> None:
        pass


class FakeResource:
    """Fake PostgresResource running each statement through one scripted cursor."""

    def __init__(self, results: list[tuple | None] | None = None) -> None:
        self.cursor_obj = FakeCursor(results or [])

    @contextmanager
    def cursor(self):
        yield self.cursor_obj

    @contextmanager
    def transactional_cursor(self):
        yield self.cursor_obj


def _ranges() -> list[SourceOffsetRange]:
    return [SourceOffsetRange(topic="events", partition=0, start_offset=100, end_offset=200)]


def _row(checkpoint_id: str, **overrides) -> tuple:
    values = {
        "checkpoint_id": checkpoint_id,
        "source_id": "kafka:events",
        "target_table": "bronze.events",
        "status": "claimed",
        "ranges": [{"topic": "events", "partition": 0, "start_offset": 100, "end_offset": 200}],
        "snapshot_id": None,
        "release_id": None,
        "idempotency_key": "kafka:events:0:100",
        "failure_reason": None,
        "updated_at": None,
    }
    values.update(overrides)
    return tuple(values.values())


def test_store_implements_checkpoint_protocol() -> None:
    store = PostgresIngestionCheckpointStore(resource=FakeResource(), table_ensured=True)
    assert isinstance(store, IngestionCheckpointStore)


def test_claim_persists_a_new_checkpoint_and_resumes_it() -> None:
    # Execute the claim SQL against an empty local database. Only adapt
    # PostgreSQL placeholders, JSON decoding and the advisory-lock primitive.
    database = sqlite3.connect(":memory:")
    database.execute("ATTACH DATABASE ':memory:' AS phlo")
    database.execute("""CREATE TABLE phlo.ingestion_checkpoints (
        checkpoint_id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
        source_id TEXT, target_table TEXT, status TEXT, ranges TEXT,
        snapshot_id INTEGER, release_id TEXT, idempotency_key TEXT UNIQUE,
        failure_reason TEXT, updated_at TEXT)""")

    class ClaimCursor:
        def __init__(self):
            self.cursor = database.cursor()
            self.locked = False

        def execute(self, statement, params):
            if "pg_advisory_xact_lock" in statement:
                self.locked = True
                return
            assert self.locked, "claims must acquire their transaction lock first"
            self.cursor.execute(statement.replace("%s", "?"), params)

        def fetchone(self):
            row = self.cursor.fetchone()
            return (*row[:4], json.loads(row[4]), *row[5:]) if row else None

    class ClaimResource:
        @contextmanager
        def transactional_cursor(self):
            with database:
                yield ClaimCursor()

    try:
        store = PostgresIngestionCheckpointStore(resource=ClaimResource(), table_ensured=True)
        request = dict(
            source_id="kafka:events",
            target_table="bronze.events",
            ranges=_ranges(),
            idempotency_key="kafka:events:0:100",
        )
        record = store.claim(**request)
        assert record.status == "claimed"
        assert record.ranges == tuple(_ranges())
        assert store.claim(**request) == record
        persisted = database.execute(
            "SELECT source_id, target_table, status, ranges FROM phlo.ingestion_checkpoints"
        ).fetchall()
        assert len(persisted) == 1
        assert persisted[0][:3] == ("kafka:events", "bronze.events", "claimed")
        assert json.loads(persisted[0][3]) == [
            {"topic": "events", "partition": 0, "start_offset": 100, "end_offset": 200}
        ]
    finally:
        database.close()


def test_claim_returns_existing_checkpoint_for_same_idempotency_key() -> None:
    resource = FakeResource(results=[_row("cp-existing")])
    store = PostgresIngestionCheckpointStore(resource=resource, table_ensured=True)
    record = store.claim(
        source_id="kafka:events",
        target_table="bronze.events",
        ranges=_ranges(),
        idempotency_key="kafka:events:0:100",
    )
    assert record.checkpoint_id == "cp-existing"
    inserts = [s for s in resource.cursor_obj.statements if "INSERT INTO" in s]
    assert not inserts, "an existing claim must be resumed, not duplicated"


def test_record_snapshot_and_commit_follow_the_lifecycle() -> None:
    resource = FakeResource(
        results=[
            _row("cp-1", status="staged", snapshot_id=42),
            _row("cp-1", status="committed", snapshot_id=42),
        ]
    )
    store = PostgresIngestionCheckpointStore(resource=resource, table_ensured=True)
    staged = store.record_snapshot(checkpoint_id="cp-1", snapshot_id=42, release_id="release-1")
    assert staged.status == "staged"
    assert staged.snapshot_id == 42
    committed = store.commit(checkpoint_id="cp-1")
    assert committed.status == "committed"


def test_commit_without_snapshot_raises_and_refuses_to_advance() -> None:
    resource = FakeResource(results=[])
    store = PostgresIngestionCheckpointStore(resource=resource, table_ensured=True)
    with pytest.raises(ValueError, match="no audited snapshot"):
        store.commit(checkpoint_id="cp-missing")


def test_record_snapshot_on_closed_checkpoint_fails_closed() -> None:
    resource = FakeResource(results=[])
    store = PostgresIngestionCheckpointStore(resource=resource, table_ensured=True)
    with pytest.raises(ValueError, match="not open"):
        store.record_snapshot(checkpoint_id="cp-closed", snapshot_id=7)


def test_failed_checkpoint_can_restage_after_resolution() -> None:
    """After the blocking issue (e.g. schema migration) is fixed, retry works."""
    resource = FakeResource(
        results=[_row("cp-1", status="staged", snapshot_id=9, failure_reason=None)]
    )
    store = PostgresIngestionCheckpointStore(resource=resource, table_ensured=True)
    staged = store.record_snapshot(checkpoint_id="cp-1", snapshot_id=9)
    assert staged.status == "staged"
    update_sql = " ".join(resource.cursor_obj.statements[-1].split())
    assert "status IN" in update_sql
    # The restageable set is bound as a parameter and includes failed rows.
    assert resource.cursor_obj.params[-1][-1] == ("claimed", "staged", "failed")


def test_fail_records_reason_but_keeps_ranges() -> None:
    reason = "schema policy violation"
    resource = FakeResource(results=[_row("cp-1", status="failed", failure_reason=reason)])
    store = PostgresIngestionCheckpointStore(resource=resource, table_ensured=True)
    record = store.fail(checkpoint_id="cp-1", reason=reason)
    assert record.status == "failed"
    assert record.failure_reason == reason
    assert record.ranges, "failed checkpoints retain their claimed ranges"


def test_provider_registers_checkpoint_resource() -> None:
    provider = PostgresResourceProvider()
    names = [spec.name for spec in provider.get_resources()]
    assert "postgres" in names
    assert "ingestion_checkpoints" in names


def test_real_resource_is_used_when_not_injected() -> None:
    store = PostgresIngestionCheckpointStore()
    assert store._resource is None
    assert store._ensure_resource() is not None
