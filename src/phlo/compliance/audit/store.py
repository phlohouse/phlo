"""Audit store implementations.

Provides Postgres-based audit store and basic in-memory store for testing.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from phlo.compliance.audit.sealed import (
        ChainVerificationResult,
        SealedAuditRecord,
    )


class InMemoryAuditStore:
    """In-memory audit store for testing.

    Not suitable for production use.
    """

    is_durable = False

    def __init__(self) -> None:
        self._records: dict[str, list[SealedAuditRecord]] = {}
        self._lock = threading.Lock()

    def append(self, record: SealedAuditRecord) -> None:
        """Append a sealed record to the store, grouped by event surface."""
        surface = record.event.surface or "unknown"
        with self._lock:
            if surface not in self._records:
                self._records[surface] = []
            self._records[surface].append(record)

    def get_last(self, surface: str) -> SealedAuditRecord | None:
        """Return the most recent sealed record for ``surface``, or None."""
        with self._lock:
            records = self._records.get(surface, [])
            return records[-1] if records else None

    def query(
        self,
        surface: str,
        after: int | None = None,
        before: int | None = None,
        limit: int = 1000,
    ) -> list[SealedAuditRecord]:
        """Return stored records matching the given filters."""
        with self._lock:
            records = self._records.get(surface, [])
            filtered = [
                r
                for r in records
                if (after is None or r.sequence_number > after)
                and (before is None or r.sequence_number < before)
            ]
            return filtered[:limit]

    def verify_chain(
        self,
        surface: str,
        hmac_key: bytes | None = None,
    ) -> ChainVerificationResult:
        """Verify the hash chain integrity of records for ``surface``."""
        from phlo.compliance.audit.sealed import (
            GENESIS_HASH,
            ChainVerificationResult,
            compute_record_hash,
        )

        with self._lock:
            records = self._records.get(surface, [])
            if not records:
                return ChainVerificationResult(
                    valid=True,
                    surface=surface,
                    total_records=0,
                )

            expected_prev = GENESIS_HASH
            for record in records:
                if record.previous_hash != expected_prev:
                    return ChainVerificationResult(
                        valid=False,
                        surface=surface,
                        total_records=len(records),
                        first_invalid_sequence=record.sequence_number,
                        error_message=f"Previous hash mismatch at sequence {record.sequence_number}",
                    )
                expected_hash = compute_record_hash(
                    record.event,
                    record.sequence_number,
                    record.previous_hash,
                    hmac_key=hmac_key,
                )
                if record.record_hash != expected_hash:
                    return ChainVerificationResult(
                        valid=False,
                        surface=surface,
                        total_records=len(records),
                        first_invalid_sequence=record.sequence_number,
                        error_message=f"Record hash mismatch at sequence {record.sequence_number}",
                    )
                expected_prev = record.record_hash

            return ChainVerificationResult(
                valid=True,
                surface=surface,
                total_records=len(records),
                verified_hashes=[r.record_hash for r in records],
            )


class PostgresAuditStore:
    """PostgreSQL-based audit store.

    Uses the metadata Postgres database for storage.
    Table: compliance_audit_log (append-only).
    """

    is_durable = True

    def __init__(self, connection) -> None:
        """Initialize the store with a psycopg2 connection or pool."""
        self._conn = connection
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the audit log table if it doesn't exist."""
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS compliance_audit_log (
                    id SERIAL PRIMARY KEY,
                    surface VARCHAR(255) NOT NULL,
                    sequence_number BIGINT NOT NULL,
                    sealed_at TIMESTAMPTZ NOT NULL,
                    previous_hash VARCHAR(64) NOT NULL,
                    record_hash VARCHAR(64) NOT NULL,
                    event_data JSONB NOT NULL,
                    UNIQUE(surface, sequence_number)
                )
                """,
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_compliance_audit_log_surface_seq
                ON compliance_audit_log(surface, sequence_number)
                """,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def append(self, record: SealedAuditRecord) -> None:
        """Persist a sealed record to the append-only audit table."""
        event_data = json.dumps(record.event.to_dict())
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO compliance_audit_log
                (surface, sequence_number, sealed_at, previous_hash, record_hash, event_data)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    record.event.surface,
                    record.sequence_number,
                    record.sealed_at,
                    record.previous_hash,
                    record.record_hash,
                    event_data,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def get_last(self, surface: str) -> SealedAuditRecord | None:
        """Return the newest stored record for ``surface``, or None."""
        from phlo.audit.events import CanonicalAuditEvent

        cursor = self._conn.cursor()
        try:
            cursor.execute(
                """
                SELECT sequence_number, sealed_at, previous_hash, record_hash, event_data
                FROM compliance_audit_log
                WHERE surface = %s
                ORDER BY sequence_number DESC
                LIMIT 1
                """,
                (surface,),
            )
            row = cursor.fetchone()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

        if not row:
            return None

        from phlo.compliance.audit.sealed import SealedAuditRecord

        event = CanonicalAuditEvent(**json.loads(row[4]))
        return SealedAuditRecord(
            sequence_number=row[0],
            sealed_at=row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
            previous_hash=row[2],
            record_hash=row[3],
            event=event,
        )

    def query(
        self,
        surface: str,
        after: int | None = None,
        before: int | None = None,
        limit: int = 1000,
    ) -> list[SealedAuditRecord]:
        """Return stored records matching the given filters."""
        from phlo.audit.events import CanonicalAuditEvent

        query_sql = """
            SELECT sequence_number, sealed_at, previous_hash, record_hash, event_data
            FROM compliance_audit_log
            WHERE surface = %s
        """
        params: list[Any] = [surface]

        if after is not None:
            query_sql += " AND sequence_number > %s"
            params.append(after)
        if before is not None:
            query_sql += " AND sequence_number < %s"
            params.append(before)

        query_sql += " ORDER BY sequence_number ASC LIMIT %s"
        params.append(limit)

        cursor = self._conn.cursor()
        try:
            cursor.execute(query_sql, tuple(params))
            rows = cursor.fetchall()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

        from phlo.compliance.audit.sealed import SealedAuditRecord

        results = []
        for row in rows:
            event = CanonicalAuditEvent(**json.loads(row[4]))
            results.append(
                SealedAuditRecord(
                    sequence_number=row[0],
                    sealed_at=row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
                    previous_hash=row[2],
                    record_hash=row[3],
                    event=event,
                )
            )
        return results

    def verify_chain(
        self,
        surface: str,
        hmac_key: bytes | None = None,
    ) -> ChainVerificationResult:
        """Verify the hash chain integrity of all stored records."""
        from phlo.compliance.audit.sealed import (
            GENESIS_HASH,
            ChainVerificationResult,
            compute_record_hash,
        )

        records = self.query(surface, limit=100000)
        if not records:
            return ChainVerificationResult(
                valid=True,
                surface=surface,
                total_records=0,
            )

        expected_prev = GENESIS_HASH
        for record in records:
            if record.previous_hash != expected_prev:
                return ChainVerificationResult(
                    valid=False,
                    surface=surface,
                    total_records=len(records),
                    first_invalid_sequence=record.sequence_number,
                    error_message=f"Previous hash mismatch at sequence {record.sequence_number}",
                )
            expected_hash = compute_record_hash(
                record.event,
                record.sequence_number,
                record.previous_hash,
                hmac_key=hmac_key,
            )
            if record.record_hash != expected_hash:
                return ChainVerificationResult(
                    valid=False,
                    surface=surface,
                    total_records=len(records),
                    first_invalid_sequence=record.sequence_number,
                    error_message=f"Record hash mismatch at sequence {record.sequence_number}",
                )
            expected_prev = record.record_hash

        return ChainVerificationResult(
            valid=True,
            surface=surface,
            total_records=len(records),
            verified_hashes=[r.record_hash for r in records],
        )
