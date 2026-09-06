"""Durable ingestion checkpoints backed by Phlo PostgreSQL.

Implements the neutral :class:`~phlo.capabilities.interfaces.IngestionCheckpointStore`
contract on the platform's existing PostgreSQL service so stream consumers
(Kafka today) can persist claimed offset ranges, bind them to output Iceberg
snapshots, and commit only after the snapshot is audited and promoted.

Claims are serialized per idempotency key with a PostgreSQL advisory
transaction lock plus a unique partial index, so two workers racing on the
same range cannot both hold an open claim.
"""

from __future__ import annotations

import json
from typing import Any

from phlo.capabilities.interfaces import (
    CheckpointRecord,
    IngestionCheckpointStore,
    SourceOffsetRange,
)
from phlo.logging import get_logger
from phlo_postgres.resource import PostgresResource

logger = get_logger(__name__)

_SCHEMA_NAME = "phlo"
_TABLE_NAME = "ingestion_checkpoints"
_OPEN_STATUSES = ("claimed", "staged", "failed")
_RESTAGABLE_STATUSES = ("claimed", "staged", "failed")

_DDL_STATEMENTS = (
    f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA_NAME}",
    f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA_NAME}.{_TABLE_NAME} (
        checkpoint_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_id TEXT NOT NULL,
        target_table TEXT NOT NULL,
        status TEXT NOT NULL,
        ranges JSONB NOT NULL DEFAULT '[]'::jsonb,
        snapshot_id BIGINT,
        release_id TEXT,
        idempotency_key TEXT,
        failure_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    f"""
    CREATE UNIQUE INDEX IF NOT EXISTS {_TABLE_NAME}_idempotency_key_idx
    ON {_SCHEMA_NAME}.{_TABLE_NAME} (idempotency_key)
    WHERE idempotency_key IS NOT NULL
    """,
    f"""
    CREATE INDEX IF NOT EXISTS {_TABLE_NAME}_source_status_idx
    ON {_SCHEMA_NAME}.{_TABLE_NAME} (source_id, status)
    """,
)

_SELECT_COLUMNS = (
    "checkpoint_id, source_id, target_table, status, ranges, snapshot_id, "
    "release_id, idempotency_key, failure_reason, updated_at"
)


def _ranges_to_json(ranges: list[SourceOffsetRange]) -> list[dict[str, Any]]:
    return [
        {
            "topic": item.topic,
            "partition": item.partition,
            "start_offset": item.start_offset,
            "end_offset": item.end_offset,
        }
        for item in ranges
    ]


def _ranges_from_json(raw: Any) -> tuple[SourceOffsetRange, ...]:
    if not raw:
        return ()
    return tuple(
        SourceOffsetRange(
            topic=item["topic"],
            partition=int(item["partition"]),
            start_offset=int(item["start_offset"]),
            end_offset=int(item["end_offset"]),
        )
        for item in raw
    )


def _record_from_row(row: tuple) -> CheckpointRecord:
    (
        checkpoint_id,
        source_id,
        target_table,
        status,
        ranges,
        snapshot_id,
        release_id,
        idempotency_key,
        failure_reason,
        updated_at,
    ) = row
    return CheckpointRecord(
        checkpoint_id=str(checkpoint_id),
        source_id=source_id,
        target_table=target_table,
        status=status,
        ranges=_ranges_from_json(ranges),
        snapshot_id=snapshot_id,
        release_id=release_id,
        idempotency_key=idempotency_key,
        failure_reason=failure_reason,
        updated_at=updated_at,
    )


class PostgresIngestionCheckpointStore(IngestionCheckpointStore):
    """PostgreSQL-backed durable checkpoint store for stream ingestion."""

    def __init__(
        self,
        *,
        resource: PostgresResource | None = None,
        table_ensured: bool = False,
    ) -> None:
        self._resource = resource
        self._table_ensured = table_ensured

    def _ensure_resource(self) -> PostgresResource:
        if self._resource is None:
            self._resource = PostgresResource()
        return self._resource

    def _ensure_table(self) -> None:
        if self._table_ensured:
            return
        resource = self._ensure_resource()
        with resource.transactional_cursor() as cursor:
            for statement in _DDL_STATEMENTS:
                cursor.execute(statement)
        self._table_ensured = True

    def claim(
        self,
        *,
        source_id: str,
        target_table: str,
        ranges: list[SourceOffsetRange],
        idempotency_key: str | None = None,
    ) -> CheckpointRecord:
        """Record an exclusive claim on the supplied ranges.

        Existing claims with the same idempotency key are returned unchanged
        so a retry after a crash resumes the same checkpoint instead of
        double-claiming the range.
        """
        self._ensure_table()
        resource = self._ensure_resource()
        lock_key = idempotency_key or f"{source_id}:{target_table}"
        with resource.transactional_cursor() as cursor:
            # The advisory lock serialises concurrent claims for the same key
            # before the existence check, closing the check-then-insert race.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
            if idempotency_key:
                cursor.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM {_SCHEMA_NAME}.{_TABLE_NAME} "
                    "WHERE idempotency_key = %s",
                    (idempotency_key,),
                )
                row = cursor.fetchone()
                if row:
                    return _record_from_row(row)
            cursor.execute(
                f"""
                INSERT INTO {_SCHEMA_NAME}.{_TABLE_NAME}
                    (source_id, target_table, status, ranges, idempotency_key)
                VALUES (%s, %s, 'claimed', %s, %s)
                RETURNING {_SELECT_COLUMNS}
                """,
                (
                    source_id,
                    target_table,
                    json.dumps(_ranges_to_json(ranges)),
                    idempotency_key,
                ),
            )
            return _record_from_row(cursor.fetchone())

    def record_snapshot(
        self,
        *,
        checkpoint_id: str,
        snapshot_id: int | str,
        release_id: str | None = None,
    ) -> CheckpointRecord:
        """Bind the claimed ranges to the output snapshot that represents them.

        A ``failed`` checkpoint may re-stage once its blocking issue (for
        example a schema migration) is resolved; the failure reason is
        cleared on the transition.
        """
        self._ensure_table()
        resource = self._ensure_resource()
        with resource.transactional_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {_SCHEMA_NAME}.{_TABLE_NAME}
                SET status = 'staged', snapshot_id = %s, release_id = %s,
                    failure_reason = NULL, updated_at = NOW()
                WHERE checkpoint_id = %s AND status IN %s
                RETURNING {_SELECT_COLUMNS}
                """,
                (int(snapshot_id), release_id, checkpoint_id, _RESTAGABLE_STATUSES),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError(
                f"Checkpoint {checkpoint_id!r} is not open; refusing to bind a snapshot."
            )
        return _record_from_row(row)

    def commit(self, *, checkpoint_id: str) -> CheckpointRecord:
        """Mark a snapshot-bound checkpoint as durably committed."""
        self._ensure_table()
        resource = self._ensure_resource()
        with resource.transactional_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {_SCHEMA_NAME}.{_TABLE_NAME}
                SET status = 'committed', updated_at = NOW()
                WHERE checkpoint_id = %s AND status = 'staged' AND snapshot_id IS NOT NULL
                RETURNING {_SELECT_COLUMNS}
                """,
                (checkpoint_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError(
                f"Checkpoint {checkpoint_id!r} has no audited snapshot; refusing to commit."
            )
        return _record_from_row(row)

    def fail(self, *, checkpoint_id: str, reason: str) -> CheckpointRecord:
        """Mark a checkpoint failed while retaining its claimed ranges."""
        self._ensure_table()
        resource = self._ensure_resource()
        with resource.transactional_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {_SCHEMA_NAME}.{_TABLE_NAME}
                SET status = 'failed', failure_reason = %s, updated_at = NOW()
                WHERE checkpoint_id = %s AND status IN %s
                RETURNING {_SELECT_COLUMNS}
                """,
                (reason, checkpoint_id, _OPEN_STATUSES),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Checkpoint {checkpoint_id!r} is not open; cannot mark failed.")
        return _record_from_row(row)

    def latest_committed(self, *, source_id: str, target_table: str) -> CheckpointRecord | None:
        """Return the newest committed checkpoint for one source/table pair."""
        self._ensure_table()
        resource = self._ensure_resource()
        with resource.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_SELECT_COLUMNS} FROM {_SCHEMA_NAME}.{_TABLE_NAME}
                WHERE source_id = %s AND target_table = %s AND status = 'committed'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (source_id, target_table),
            )
            row = cursor.fetchone()
        return _record_from_row(row) if row else None

    def find_by_idempotency_key(self, *, idempotency_key: str) -> CheckpointRecord | None:
        """Resolve an existing claim by its deterministic idempotency key."""
        self._ensure_table()
        resource = self._ensure_resource()
        with resource.cursor() as cursor:
            cursor.execute(
                f"SELECT {_SELECT_COLUMNS} FROM {_SCHEMA_NAME}.{_TABLE_NAME} "
                "WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            row = cursor.fetchone()
        return _record_from_row(row) if row else None

    def list_open(self, *, source_id: str) -> list[CheckpointRecord]:
        """List claimed-but-uncommitted checkpoints needing reconciliation."""
        self._ensure_table()
        resource = self._ensure_resource()
        with resource.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_SELECT_COLUMNS} FROM {_SCHEMA_NAME}.{_TABLE_NAME}
                WHERE source_id = %s AND status IN %s
                ORDER BY updated_at ASC
                """,
                (source_id, _OPEN_STATUSES),
            )
            rows = cursor.fetchall()
        return [_record_from_row(row) for row in rows]
