"""Tests for PostgresAuditStore: schema DDL commit/rollback and sealed
record handling with mocked connections."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from phlo.audit.events import CanonicalAuditEvent
from phlo.compliance.audit import GENESIS_HASH, SealedAuditRecord
from phlo.compliance.audit.store import PostgresAuditStore


def test_postgres_audit_store_commits_schema_creation() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    PostgresAuditStore(conn)

    assert cursor.execute.call_count == 2
    conn.commit.assert_called_once()
    cursor.close.assert_called_once()


def test_postgres_audit_store_rolls_back_failed_schema_creation() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = RuntimeError("ddl failed")

    try:
        PostgresAuditStore(conn)
    except RuntimeError as exc:
        assert str(exc) == "ddl failed"
    else:
        raise AssertionError("Expected schema creation failure")

    conn.rollback.assert_called_once()
    cursor.close.assert_called_once()
    conn.commit.assert_not_called()


def test_postgres_audit_store_rolls_back_failed_append() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    store = PostgresAuditStore(conn)
    conn.reset_mock()
    cursor.reset_mock()
    cursor.execute.side_effect = RuntimeError("insert failed")

    record = MagicMock()
    record.event.to_dict.return_value = {"event_id": "evt-1"}
    record.event.surface = "phlo-api"
    record.sequence_number = 1
    record.sealed_at = "2026-04-20T00:00:00Z"
    record.previous_hash = "prev"
    record.record_hash = "hash"

    try:
        store.append(record)
    except RuntimeError as exc:
        assert str(exc) == "insert failed"
    else:
        raise AssertionError("Expected append failure")

    conn.rollback.assert_called_once()
    cursor.close.assert_called_once()
    conn.commit.assert_not_called()


def test_postgres_audit_store_appends_a_sealed_record() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    store = PostgresAuditStore(conn)
    conn.reset_mock()
    cursor.reset_mock()

    event = CanonicalAuditEvent(event_type="authorization", surface="phlo-api")
    record = SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)

    store.append(record)

    assert "INSERT INTO compliance_audit_log" in cursor.execute.call_args.args[0]
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
    cursor.close.assert_called_once()


def test_postgres_audit_store_verify_chain_detects_payload_tampering() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    store = PostgresAuditStore(conn)
    conn.reset_mock()
    cursor.reset_mock()

    first_event = CanonicalAuditEvent(
        event_type="authorization",
        surface="phlo-api",
        actor_subject="alice@example.com",
        action="dataset.read",
        decision="allow",
    )
    first_record = SealedAuditRecord.seal(
        first_event, sequence_number=1, previous_hash=GENESIS_HASH
    )

    second_event = CanonicalAuditEvent(
        event_type="authorization",
        surface="phlo-api",
        actor_subject="bob@example.com",
        action="dataset.read",
        decision="allow",
    )
    second_record = SealedAuditRecord.seal(
        second_event,
        sequence_number=2,
        previous_hash=first_record.record_hash,
    )

    tampered_event = CanonicalAuditEvent(
        event_type="authorization",
        surface="phlo-api",
        actor_subject="mallory@example.com",
        action="dataset.read",
        decision="allow",
    )

    cursor.fetchall.return_value = [
        (
            first_record.sequence_number,
            first_record.sealed_at,
            first_record.previous_hash,
            first_record.record_hash,
            json.dumps(first_record.event.to_dict()),
        ),
        (
            second_record.sequence_number,
            second_record.sealed_at,
            second_record.previous_hash,
            second_record.record_hash,
            json.dumps(tampered_event.to_dict()),
        ),
    ]

    result = store.verify_chain("phlo-api")

    assert result.valid is False
    assert result.first_invalid_sequence == 2
    assert result.error_message == "Record hash mismatch at sequence 2"
