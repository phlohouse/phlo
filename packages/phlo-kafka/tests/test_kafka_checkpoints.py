"""Tests for the Kafka checkpoint lifecycle adapter."""

from __future__ import annotations

import pytest
from phlo.capabilities.interfaces import CheckpointRecord, SourceOffsetRange
from phlo_kafka.checkpoints import (
    KafkaCheckpointAdapter,
    idempotency_key,
)


class FakeStore:
    def __init__(self) -> None:
        self.claimed: list[dict] = []
        self.snapshots: list[dict] = []
        self.committed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def claim(self, *, source_id, target_table, ranges, idempotency_key=None):
        self.claimed.append(
            {
                "source_id": source_id,
                "target_table": target_table,
                "ranges": list(ranges),
                "idempotency_key": idempotency_key,
            }
        )
        return CheckpointRecord(
            checkpoint_id="cp-1",
            source_id=source_id,
            target_table=target_table,
            status="claimed",
            ranges=tuple(ranges),
            idempotency_key=idempotency_key,
        )

    def record_snapshot(self, *, checkpoint_id, snapshot_id, release_id=None):
        self.snapshots.append((checkpoint_id, snapshot_id, release_id))
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            source_id="s",
            target_table="t",
            status="staged",
            snapshot_id=snapshot_id,
            release_id=release_id,
        )

    def commit(self, *, checkpoint_id):
        self.committed.append(checkpoint_id)
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            source_id="s",
            target_table="t",
            status="committed",
        )

    def fail(self, *, checkpoint_id, reason):
        self.failed.append((checkpoint_id, reason))
        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            source_id="s",
            target_table="t",
            status="failed",
            failure_reason=reason,
        )

    def list_open(self, *, source_id):
        return []


def test_idempotency_key_is_deterministic_per_range() -> None:
    ranges = _ranges() + [SourceOffsetRange("events", 1, 20, 30)]
    assert idempotency_key(source_id="s", target_table="t", group_id="g", ranges=ranges) == (
        idempotency_key(source_id="s", target_table="t", group_id="g", ranges=ranges[::-1])
    )


@pytest.mark.parametrize(
    "ranges",
    [
        [SourceOffsetRange("events", 0, 100, 201)],
        [SourceOffsetRange("events", 0, 100, 200), SourceOffsetRange("events", 1, 0, 10)],
    ],
)
def test_changed_batch_does_not_resume_previous_checkpoint(ranges) -> None:
    assert idempotency_key(source_id="s", target_table="t", group_id="g", ranges=_ranges()) != (
        idempotency_key(source_id="s", target_table="t", group_id="g", ranges=ranges)
    )


def _ranges() -> list[SourceOffsetRange]:
    return [SourceOffsetRange(topic="events", partition=0, start_offset=100, end_offset=200)]


def test_claim_ranges_derives_idempotency_key_from_group() -> None:
    store = FakeStore()
    adapter = KafkaCheckpointAdapter(source_id="kafka:events", group_id="phlo-events", store=store)
    checkpoint = adapter.claim_ranges(target_table="bronze.events", ranges=_ranges())
    assert checkpoint.status == "claimed"
    assert checkpoint.ranges == tuple(_ranges())


def test_checkpoint_with_different_ranges_is_rejected() -> None:
    class MismatchedStore(FakeStore):
        def claim(self, **kwargs):
            return super().claim(**{**kwargs, "ranges": [SourceOffsetRange("events", 0, 100, 150)]})

    adapter = KafkaCheckpointAdapter(source_id="s", group_id="g", store=MismatchedStore())
    with pytest.raises(ValueError, match="does not match"):
        adapter.claim_ranges(target_table="t", ranges=_ranges())


def test_lifecycle_transitions_record_snapshot_before_commit() -> None:
    store = FakeStore()
    adapter = KafkaCheckpointAdapter(source_id="kafka:events", group_id="g", store=store)
    checkpoint = adapter.claim_ranges(target_table="bronze.events", ranges=_ranges())
    adapter.bind_snapshot(checkpoint_id=checkpoint.checkpoint_id, snapshot_id=42)
    adapter.commit(checkpoint_id=checkpoint.checkpoint_id)
    assert store.snapshots == [("cp-1", 42, None)]
    assert store.committed == ["cp-1"]


def test_fail_records_reason_and_retains_open_state() -> None:
    store = FakeStore()
    adapter = KafkaCheckpointAdapter(source_id="kafka:events", group_id="g", store=store)
    adapter.fail(checkpoint_id="cp-1", reason="schema policy violation")
    assert store.failed == [("cp-1", "schema policy violation")]
