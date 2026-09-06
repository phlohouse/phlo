"""Checkpoint lifecycle adapter binding Kafka offset ranges to Phlo state.

Resolves the neutral ``IngestionCheckpointStore`` capability (backed by Phlo
Postgres) and identifies the complete batch, including source, destination,
consumer group and every partition's half-open offset range.
"""

from __future__ import annotations

import hashlib
import json

from phlo.capabilities.interfaces import (
    CheckpointRecord,
    IngestionCheckpointStore,
    SourceOffsetRange,
)
from phlo.capabilities.resolver import resolve_capability
from phlo.exceptions import PhloConfigError


def idempotency_key(
    *, source_id: str, target_table: str, group_id: str, ranges: list[SourceOffsetRange]
) -> str:
    """Identify an exact batch independently of partition polling order."""
    if not ranges or any(item.start_offset >= item.end_offset for item in ranges):
        raise ValueError("Kafka checkpoints require nonempty offset ranges")
    batch = sorted(
        (item.topic, item.partition, item.start_offset, item.end_offset) for item in ranges
    )
    if len({(item.topic, item.partition) for item in ranges}) != len(ranges):
        raise ValueError("Kafka checkpoints require one range per topic partition")
    payload = json.dumps([source_id, target_table, group_id, batch], separators=(",", ":"))
    return f"kafka-batch-v2:{hashlib.sha256(payload.encode()).hexdigest()}"


def resolve_checkpoint_store(name: str | None = None) -> IngestionCheckpointStore:
    """Resolve the configured checkpoint store capability, failing closed."""
    resolution = resolve_capability("ingestion_checkpoints", name)
    if resolution is None:
        raise PhloConfigError(
            message="Kafka ingestion requires an ingestion checkpoint store capability.",
            suggestions=["Install phlo-postgres to enable durable ingestion checkpoints."],
        )
    return resolution.provider


class KafkaCheckpointAdapter:
    """Drives the claim→staged→committed checkpoint lifecycle for one source."""

    def __init__(
        self,
        *,
        source_id: str,
        group_id: str,
        store: IngestionCheckpointStore | None = None,
    ) -> None:
        self.source_id = source_id
        self.group_id = group_id
        self._store = store

    @property
    def store(self) -> IngestionCheckpointStore:
        if self._store is None:
            self._store = resolve_checkpoint_store()
        return self._store

    def claim_ranges(
        self, *, target_table: str, ranges: list[SourceOffsetRange]
    ) -> CheckpointRecord:
        """Claim consumed ranges; resuming a claimed range is a no-op."""
        key = idempotency_key(
            source_id=self.source_id,
            target_table=target_table,
            group_id=self.group_id,
            ranges=ranges,
        )
        checkpoint = self.store.claim(
            source_id=self.source_id,
            target_table=target_table,
            ranges=ranges,
            idempotency_key=key,
        )
        if (
            checkpoint.source_id != self.source_id
            or checkpoint.target_table != target_table
            or set(checkpoint.ranges) != set(ranges)
        ):
            raise ValueError("Checkpoint identity does not match the consumed batch")
        return checkpoint

    def bind_snapshot(
        self, *, checkpoint_id: str, snapshot_id: int | str, release_id: str | None = None
    ) -> CheckpointRecord:
        """Bind the claimed ranges to the staged output snapshot."""
        return self.store.record_snapshot(
            checkpoint_id=checkpoint_id,
            snapshot_id=snapshot_id,
            release_id=release_id,
        )

    def commit(self, *, checkpoint_id: str) -> CheckpointRecord:
        """Commit after the snapshot is audited and promoted."""
        return self.store.commit(checkpoint_id=checkpoint_id)

    def fail(self, *, checkpoint_id: str, reason: str) -> CheckpointRecord:
        """Mark a checkpoint failed; its offsets are retained, not committed."""
        return self.store.fail(checkpoint_id=checkpoint_id, reason=reason)

    def open_checkpoints(self) -> list[CheckpointRecord]:
        """List claimed-but-uncommitted checkpoints needing reconciliation."""
        return self.store.list_open(source_id=self.source_id)
