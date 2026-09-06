"""Tests for the Kafka consumer asset lifecycle (claim→stage→audit→commit)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest
from phlo.logging import get_logger
from phlo.capabilities.interfaces import CheckpointRecord, ReleaseRecord, SourceOffsetRange
from phlo_kafka.assets import (
    KafkaConsumerConfig,
    clear_kafka_assets,
    get_kafka_assets,
    ingest_batch,
    phlo_kafka_consumer,
)
from phlo_kafka.checkpoints import KafkaCheckpointAdapter, idempotency_key


class RecordingStore:
    """Fake checkpoint store exposing the full lifecycle."""

    def __init__(self) -> None:
        self.transitions: list[tuple] = []

    def claim(self, *, source_id, target_table, ranges, idempotency_key=None):
        self.transitions.append(("claim", idempotency_key))
        return CheckpointRecord(
            checkpoint_id="cp-1",
            source_id=source_id,
            target_table=target_table,
            status="claimed",
            ranges=tuple(ranges),
            idempotency_key=idempotency_key,
        )

    def record_snapshot(self, *, checkpoint_id, snapshot_id, release_id=None):
        self.transitions.append(("snapshot", snapshot_id))
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            source_id="s",
            target_table="t",
            status="staged",
            snapshot_id=snapshot_id,
            release_id=release_id,
        )

    def commit(self, *, checkpoint_id):
        self.transitions.append(("commit", checkpoint_id))
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            source_id="s",
            target_table="t",
            status="committed",
        )

    def fail(self, *, checkpoint_id, reason):
        self.transitions.append(("fail", reason))
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            source_id="s",
            target_table="t",
            status="failed",
            failure_reason=reason,
        )

    def list_open(self, *, source_id):
        return []


def _ranges() -> list[SourceOffsetRange]:
    return [SourceOffsetRange(topic="events", partition=0, start_offset=100, end_offset=200)]


def _config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        name="events",
        group="ingestion",
        topic_pattern="events",
        destination_table="bronze.events",
        unique_key=["event_id"],
    )


def test_ingest_batch_full_lifecycle_commits_checkpoint() -> None:
    store = RecordingStore()
    promoted: list[str] = []

    def stager(checkpoint_id: str, records: list[dict]) -> dict:
        store.transitions.append(("stage", checkpoint_id))
        return {"snapshot_id": 77, "rows_merged": len(records)}

    def promoter(checkpoint_id: str) -> list[ReleaseRecord]:
        promoted.append(checkpoint_id)
        return [
            ReleaseRecord(
                table_name="bronze.events",
                snapshot_id=88,
                release_id=checkpoint_id,
                revision=1,
            )
        ]

    evidence = ingest_batch(
        config=_config(),
        records=[{"event_id": "e1", "count": 1}],
        ranges=_ranges(),
        checkpoint_adapter=KafkaCheckpointAdapter(
            source_id="kafka:events", group_id="phlo-events", store=store
        ),
        stager=stager,
        promoter=promoter,
        known_fields={"event_id": "string", "count": "int"},
    )

    assert evidence["status"] == "committed"
    assert evidence["snapshot_id"] == 88
    assert evidence["release_id"] == "cp-1"
    assert store.transitions == [
        (
            "claim",
            idempotency_key(
                source_id="kafka:events",
                target_table="bronze.events",
                group_id="phlo-events",
                ranges=_ranges(),
            ),
        ),
        ("stage", "cp-1"),
        ("snapshot", 77),
        ("snapshot", 88),
        ("commit", "cp-1"),
    ]
    assert promoted == ["cp-1"]


def test_ingest_batch_without_promoter_commits_staged_snapshot() -> None:
    store = RecordingStore()

    def stager(checkpoint_id: str, records: list[dict]) -> dict:
        return {"snapshot_id": 77, "rows_merged": len(records)}

    evidence = ingest_batch(
        config=_config(),
        records=[{"event_id": "e1", "count": 1}],
        ranges=_ranges(),
        checkpoint_adapter=KafkaCheckpointAdapter(
            source_id="kafka:events", group_id="g", store=store
        ),
        stager=stager,
        known_fields={"event_id": "string", "count": "int"},
    )
    assert evidence["status"] == "committed"
    assert evidence["snapshot_id"] == 77


def test_ingest_batch_stage_failure_marks_checkpoint_failed() -> None:
    store = RecordingStore()

    def stager(checkpoint_id: str, records: list[dict]) -> dict:
        raise RuntimeError("catalog unreachable")

    with pytest.raises(RuntimeError, match="catalog unreachable"):
        ingest_batch(
            config=_config(),
            records=[{"event_id": "e1", "count": 1}],
            ranges=_ranges(),
            checkpoint_adapter=KafkaCheckpointAdapter(
                source_id="kafka:events", group_id="g", store=store
            ),
            stager=stager,
            known_fields={"event_id": "string", "count": "int"},
        )
    assert ("fail", "stage failed: catalog unreachable") in store.transitions


def test_ingest_batch_schema_violation_dead_letters_and_retains_offsets() -> None:
    store = RecordingStore()
    dead_lettered: list[tuple[str, list[dict]]] = []

    def sink(topic: str, records: list[dict]) -> int:
        dead_lettered.append((topic, records))
        return len(records)

    evidence = ingest_batch(
        config=_config(),
        records=[{"event_id": "e1", "count": "not-a-number"}],
        ranges=_ranges(),
        checkpoint_adapter=KafkaCheckpointAdapter(
            source_id="kafka:events", group_id="phlo-events", store=store
        ),
        stager=lambda checkpoint_id, records: {"snapshot_id": 1, "rows_merged": 0},
        dead_letter_sink=sink,
        known_fields={"event_id": "string", "count": "int"},
    )

    assert evidence["status"] == "dead_lettered"
    assert evidence["dead_lettered"] == 1
    assert dead_lettered[0][0] == "events.dlq"
    # No snapshot, no commit: offsets stay uncommitted for replay after migration.
    assert ("commit", "cp-1") not in store.transitions
    assert ("snapshot", 77) not in store.transitions


def test_ingest_batch_skips_already_committed_range_without_merging() -> None:
    """Crash between checkpoint commit and offset commit replays as a skip."""
    store = RecordingStore()
    adapter = KafkaCheckpointAdapter(source_id="kafka:events", group_id="g", store=store)

    def committed_claim(*, target_table, ranges):
        store.transitions.append(("claim", "already-committed"))
        return CheckpointRecord(
            checkpoint_id="cp-0",
            source_id="kafka:events",
            target_table=target_table,
            status="committed",
            ranges=tuple(ranges),
        )

    adapter.claim_ranges = committed_claim
    evidence = ingest_batch(
        config=_config(),
        records=[{"event_id": "e1", "count": 1}],
        ranges=_ranges(),
        checkpoint_adapter=adapter,
        stager=lambda checkpoint_id, records: {"snapshot_id": 1},
        known_fields={"event_id": "string", "count": "int"},
    )

    assert evidence["status"] == "already_committed"
    # No merge, no snapshot, no second commit: no duplicate logical records.
    assert store.transitions == [("claim", "already-committed")]


def test_ingest_batch_empty_records_is_no_data() -> None:
    evidence = ingest_batch(
        config=_config(),
        records=[],
        ranges=_ranges(),
        checkpoint_adapter=KafkaCheckpointAdapter(
            source_id="kafka:events", group_id="g", store=RecordingStore()
        ),
        stager=lambda checkpoint_id, records: {},
    )
    assert evidence["status"] == "no_data"


def test_consumer_registration_requires_unique_key() -> None:
    clear_kafka_assets()
    with pytest.raises(Exception, match="unique key"):
        phlo_kafka_consumer(
            name="bad",
            topic_pattern="events",
            destination_table="bronze.events",
            unique_key=[],
            group="ingestion",
        )
    clear_kafka_assets()


def test_registered_asset_emits_commit_evidence() -> None:
    clear_kafka_assets()

    class FakeKafkaClient:
        def __init__(self) -> None:
            self.committed: list[str] = []

        def consume(self, *, topic_pattern, group_id, max_records):
            return [{"event_id": "e1", "count": 1}], _ranges()

        def dead_letter_sink(self, topic, records):
            return 0

        def commit_offsets(self, ranges, *, group_id: str) -> None:
            self.committed.append(group_id)

    spec = phlo_kafka_consumer(
        name="events",
        topic_pattern="events",
        destination_table="bronze.events",
        unique_key="event_id",
        group="ingestion",
        client_factory=lambda: FakeKafkaClient(),
    )
    assert spec.key == "ingestion.events"
    assert spec.metadata["dead_letter_topic"] == "events.dlq"
    assert get_kafka_assets() == [spec]
    clear_kafka_assets()


class DurableStore(RecordingStore):
    """Retain checkpoint state across asset runs at the database seam."""

    def __init__(self):
        super().__init__()
        self.records = {}

    def claim(self, *, source_id, target_table, ranges, idempotency_key=None):
        existing = next(
            (r for r in self.records.values() if r.idempotency_key == idempotency_key), None
        )
        if existing is not None:
            return existing
        record = CheckpointRecord(
            checkpoint_id=f"cp-{len(self.records)}",
            source_id=source_id,
            target_table=target_table,
            ranges=tuple(ranges),
            idempotency_key=idempotency_key,
            status="claimed",
        )
        self.records[record.checkpoint_id] = record
        return record

    def record_snapshot(self, *, checkpoint_id, snapshot_id, release_id=None):
        self.records[checkpoint_id] = replace(
            self.records[checkpoint_id],
            status="staged",
            snapshot_id=snapshot_id,
            release_id=release_id,
        )
        return self.records[checkpoint_id]

    def commit(self, *, checkpoint_id):
        record = self.records[checkpoint_id]
        assert record.status == "staged" and record.snapshot_id is not None
        self.records[checkpoint_id] = replace(record, status="committed")
        return self.records[checkpoint_id]


@pytest.mark.parametrize(
    "retry_ranges",
    [
        _ranges(),
        [SourceOffsetRange("events", 0, 100, 201)],
        _ranges() + [SourceOffsetRange("events", 1, 0, 1)],
    ],
)
def test_asset_recovers_offset_commit_without_skipping_new_records(monkeypatch, retry_ranges):
    store = DurableStore()

    class TableStore:
        def __init__(self):
            self.rows = {}
            self.revision = 0

        def merge_parquet(self, *, table_name, data_path, unique_key):
            rows = pq.read_table(data_path).to_pylist()
            self.rows.update({row[unique_key]: row for row in rows})
            self.revision += 1
            return {"rows_inserted": len(rows)}

        def observe_table_state(self, *, table_name):
            return SimpleNamespace(revision=self.revision)

    table_store = TableStore()

    class Client:
        ranges = _ranges()
        records = [{"event_id": "first"}]
        crash = True
        committed = []

        def consume(self, **kwargs):
            return self.records, self.ranges

        def commit_offsets(self, ranges, *, group_id):
            assert any(
                record.status == "committed" and set(record.ranges) == set(ranges)
                for record in store.records.values()
            )
            if self.crash:
                raise RuntimeError("crash after checkpoint commit")
            self.committed.append(ranges)

    client = Client()
    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config", lambda: SimpleNamespace(strategy="branch")
    )
    monkeypatch.setattr(
        "phlo_kafka.assets.resolve_capability", lambda _: SimpleNamespace(provider=table_store)
    )
    monkeypatch.setattr(
        "phlo_kafka.checkpoints.resolve_capability", lambda *args: SimpleNamespace(provider=store)
    )
    clear_kafka_assets()
    try:
        spec = phlo_kafka_consumer(
            name="events",
            topic_pattern="events",
            destination_table="bronze.events",
            unique_key="event_id",
            group="ingestion",
            client_factory=lambda: client,
        )
        runtime = SimpleNamespace(logger=get_logger(__name__))
        with pytest.raises(RuntimeError, match="crash after checkpoint commit"):
            list(spec.run.fn(runtime))
        assert table_store.rows == {"first": {"event_id": "first"}}
        assert client.committed == []

        client.crash = False
        client.ranges = retry_ranges
        changed_batch = retry_ranges != _ranges()
        client.records = (
            [{"event_id": "first"}, {"event_id": "new"}] if changed_batch else client.records
        )
        result = list(spec.run.fn(runtime))[0]

        assert result.metadata["status"] == ("committed" if changed_batch else "already_committed")
        assert client.committed == [retry_ranges]
        assert set(table_store.rows) == ({"first", "new"} if changed_batch else {"first"})
        assert table_store.revision == (2 if changed_batch else 1)
    finally:
        clear_kafka_assets()
