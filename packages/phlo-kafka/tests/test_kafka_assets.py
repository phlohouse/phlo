"""Tests for the Kafka consumer asset lifecycle (claim→stage→audit→commit)."""

from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest
from phlo.logging import get_logger
from phlo.capabilities.interfaces import (
    CheckpointRecord,
    ReleaseRecord,
    SnapshotPromotionCatalog,
    SourceOffsetRange,
)
from phlo.exceptions import PhloConfigError
from phlo_kafka.assets import (
    KafkaConsumerConfig,
    clear_kafka_assets,
    get_kafka_assets,
    ingest_batch,
    phlo_kafka_consumer,
)
from phlo_kafka.checkpoints import KafkaCheckpointAdapter, idempotency_key


def test_legacy_stager_default_keeps_existing_call_signature():
    from phlo_kafka.assets import _make_stager

    written = []

    def merge(*, table_name, data_path, unique_key):
        written.extend(pq.read_table(data_path).to_pylist())
        return {"rows_inserted": 1}

    provider = SimpleNamespace(
        merge_parquet=merge,
        observe_table_state=lambda **kwargs: SimpleNamespace(revision="snapshot"),
    )
    config = KafkaConsumerConfig("legacy", "raw", "events", "raw.events", ["id"])
    assert (
        _make_stager(config, None, provider)("checkpoint", [{"id": "1", "extra": "keep"}])[
            "snapshot_id"
        ]
        == "snapshot"
    )
    assert written == [{"id": "1", "extra": "keep"}]


def test_stager_rejects_misadvertised_provider_before_staging():
    from phlo.capabilities.interfaces import TableStoreSupport
    from phlo_kafka.assets import _make_stager

    provider = SimpleNamespace(
        support=TableStoreSupport(schema_policies=frozenset({"additive"})),
        merge_parquet=lambda *, table_name, data_path, unique_key: pytest.fail("unexpected write"),
    )
    config = KafkaConsumerConfig("probe", "raw", "events", "raw.events", ["id"])
    with pytest.raises(PhloConfigError, match="merge_parquet cannot accept schema_policy"):
        _make_stager(config, None, provider)


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


@pytest.mark.parametrize("receipt_format", ["record", "dict"])
def test_ingest_batch_full_lifecycle_commits_checkpoint(receipt_format) -> None:
    store = RecordingStore()
    promoted: list[str] = []

    def stager(checkpoint_id: str, records: list[dict]) -> dict:
        store.transitions.append(("stage", checkpoint_id))
        return {"snapshot_id": 77, "rows_merged": len(records)}

    def promoter(checkpoint_id: str):
        promoted.append(checkpoint_id)
        receipts = [
            ReleaseRecord(
                table_name="bronze.other",
                snapshot_id=99,
                release_id=checkpoint_id,
                revision=1,
            ),
            ReleaseRecord(
                table_name="bronze.events",
                snapshot_id=100,
                release_id="other-checkpoint",
                revision=1,
            ),
            ReleaseRecord(
                table_name="bronze.events",
                snapshot_id=88,
                release_id=checkpoint_id,
                revision=1,
            ),
        ]
        if receipt_format == "dict":
            return [asdict(receipt) for receipt in receipts]
        return receipts

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


@pytest.mark.parametrize(
    "receipts",
    [
        [],
        [{"table_name": "bronze.other", "release_id": "cp-1", "snapshot_id": 88}],
        [{"table_name": "bronze.events", "release_id": "other", "snapshot_id": 88}],
        [{"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": None}],
        [{"table_name": "bronze.events", "release_id": "cp-1"}],
        [
            {"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": 88},
            {"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": 88},
        ],
        [
            {"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": 88},
            {"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": 99},
        ],
        [
            {"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": 88},
            {"table_name": "bronze.events", "release_id": "cp-1", "snapshot_id": None},
        ],
    ],
    ids=[
        "empty",
        "wrong-table",
        "wrong-release",
        "null-snapshot",
        "missing-snapshot",
        "duplicate",
        "conflicting",
        "ambiguous-null",
    ],
)
def test_ingest_batch_rejects_invalid_promotion_receipts(receipts) -> None:
    store = RecordingStore()
    with pytest.raises(ValueError, match="exactly one matching release") as error:
        ingest_batch(
            config=_config(),
            records=[{"event_id": "e1"}],
            ranges=_ranges(),
            checkpoint_adapter=KafkaCheckpointAdapter(
                source_id="kafka:events", group_id="g", store=store
            ),
            stager=lambda checkpoint_id, records: {"snapshot_id": 77},
            promoter=lambda checkpoint_id: receipts,
        )

    assert ("commit", "cp-1") not in store.transitions
    assert [item for item in store.transitions if item[0] == "snapshot"] == [("snapshot", 77)]
    assert store.transitions[-1] == ("fail", f"promote failed: {error.value}")


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
    with pytest.raises(PhloConfigError, match="unique key"):
        phlo_kafka_consumer(
            name="bad",
            topic_pattern="events",
            destination_table="bronze.events",
            unique_key=[],
            group="ingestion",
        )
    clear_kafka_assets()


def test_registered_asset_exposes_provider_neutral_metadata() -> None:
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
    assert {"kafka", "ingestion"} <= set(spec.kinds)
    assert spec.tags["provider"] == "kafka"
    assert spec.metadata["provider"] == "kafka"
    assert spec.metadata["kafka_topic"] == "events"
    assert spec.metadata["destination_table"] == "bronze.events"
    assert spec.metadata["unique_key"] == ["event_id"]
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
            failure_reason=None,
        )
        return self.records[checkpoint_id]

    def commit(self, *, checkpoint_id):
        record = self.records[checkpoint_id]
        assert record.status == "staged" and record.snapshot_id is not None
        self.records[checkpoint_id] = replace(record, status="committed")
        return self.records[checkpoint_id]

    def fail(self, *, checkpoint_id, reason):
        self.records[checkpoint_id] = replace(
            self.records[checkpoint_id], status="failed", failure_reason=reason
        )
        return self.records[checkpoint_id]


@pytest.mark.parametrize("first_outcome", ["empty", "error", "success"])
def test_asset_scopes_promotion_and_retries_without_committing_offsets(monkeypatch, first_outcome):
    store = DurableStore()

    class Catalog(SnapshotPromotionCatalog):
        outcome = first_outcome
        candidates = {"bronze.other": 99}
        releases = {}

        def create_candidate(self, *, table_name, run_id):
            self.candidates.setdefault(table_name, 77)

        def merge_rows_into_candidate(self, *, table_name, run_id, rows, unique_key):
            return {"snapshot_id": self.candidates[table_name], "rows_merged": len(rows)}

        def promote_candidates(self, *, namespace, release_id, tables=None):
            assert namespace == "pipeline-run-cp-0" and release_id == "cp-0"
            if self.outcome == "error":
                raise RuntimeError("catalog unavailable")
            if self.outcome == "empty":
                return []
            receipts = []
            for table in list(self.candidates) if tables is None else tables:
                self.candidates.pop(table)
                receipt = ReleaseRecord(table, 88, release_id, revision=1)
                self.releases[table] = receipt
                receipts.append(receipt)
            return receipts

    catalog = Catalog()

    class Client:
        committed = []

        def consume(self, **kwargs):
            return [{"event_id": "e1"}], _ranges()

        def commit_offsets(self, ranges, *, group_id):
            assert store.records["cp-0"].status == "committed"
            self.committed.append(ranges)

    client = Client()
    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config", lambda: SimpleNamespace(strategy="snapshot")
    )
    monkeypatch.setattr(
        "phlo_kafka.assets.resolve_capability",
        lambda _: SimpleNamespace(
            provider=catalog,
            support=SimpleNamespace(supports_promote=True, supports_snapshots=True),
        ),
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
        if first_outcome != "success":
            with pytest.raises((ValueError, RuntimeError)) as error:
                list(spec.run.fn(runtime))
            failed = store.records["cp-0"]
            assert (failed.status, failed.snapshot_id, failed.release_id) == ("failed", 77, None)
            assert failed.failure_reason == f"promote failed: {error.value}"
            assert client.committed == []
            assert catalog.releases == {}
            catalog.outcome = "success"

        result = list(spec.run.fn(runtime))[0]

        assert catalog.candidates == {"bronze.other": 99}
        assert set(catalog.releases) == {"bronze.events"}
        checkpoint = store.records["cp-0"]
        assert (checkpoint.status, checkpoint.snapshot_id, checkpoint.release_id) == (
            "committed",
            88,
            "cp-0",
        )
        assert checkpoint.failure_reason is None
        assert client.committed == [_ranges()]
        assert result.metadata["snapshot_id"] == 88
        assert result.metadata["release_id"] == "cp-0"
    finally:
        clear_kafka_assets()


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
