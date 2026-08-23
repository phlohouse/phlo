"""Tests for tamper-evident audit sealing.

SealedAuditRecord hashes chain over sequence number and previous hash;
regulated mode requires an explicit PHLO_AUDIT_HMAC_KEY and never falls back
to the dev key. The in-memory store verifies the chain on read and reports
mismatches instead of silently returning tampered records.
"""

from __future__ import annotations

import json

import pytest

from phlo.audit.events import CanonicalAuditEvent
from phlo.compliance.audit import (
    GENESIS_HASH,
    InMemoryAuditStore,
    SealedAuditRecord,
    TamperEvidentAuditSink,
)


class TestSealedAuditRecord:
    """Tests for SealedAuditRecord sealing."""

    def test_seal_creates_valid_record(self) -> None:
        """Sealing an event creates a record with correct metadata."""
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )

        record = SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)

        assert record.sequence_number == 1
        assert record.previous_hash == GENESIS_HASH
        assert record.event == event
        assert len(record.record_hash) == 64
        assert record.sealed_at is not None

    def test_seal_requires_explicit_hmac_key_in_regulated_mode(self, monkeypatch) -> None:
        """Regulated audit sealing must not fall back to the deterministic dev key."""
        monkeypatch.setenv("PHLO_REGULATED", "true")
        monkeypatch.delenv("PHLO_AUDIT_HMAC_KEY", raising=False)
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )

        with pytest.raises(RuntimeError, match="PHLO_AUDIT_HMAC_KEY is required"):
            SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)

    def test_seal_uses_explicit_hmac_key_in_regulated_mode(self, monkeypatch) -> None:
        """Regulated audit sealing works when real key material is configured."""
        monkeypatch.setenv("PHLO_REGULATED", "true")
        monkeypatch.setenv("PHLO_AUDIT_HMAC_KEY", "test-audit-key")
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )

        record = SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)

        assert len(record.record_hash) == 64

    def test_seal_different_events_produce_different_hashes(self) -> None:
        """Different events produce different record hashes."""
        event1 = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )
        event2 = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="bob@example.com",
            action="dataset.read",
            decision="allow",
        )

        record1 = SealedAuditRecord.seal(event1, sequence_number=1, previous_hash=GENESIS_HASH)
        record2 = SealedAuditRecord.seal(event2, sequence_number=1, previous_hash=GENESIS_HASH)

        assert record1.record_hash != record2.record_hash

    def test_seal_same_sequence_different_previous_hash(self) -> None:
        """Different previous hashes produce different record hashes."""
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )

        record1 = SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)
        record2 = SealedAuditRecord.seal(event, sequence_number=1, previous_hash="a" * 64)

        assert record1.record_hash != record2.record_hash

    def test_to_dict_serializes_correctly(self) -> None:
        """to_dict produces a JSON-serializable dictionary."""
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )

        record = SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)
        record_dict = record.to_dict()

        assert isinstance(record_dict, dict)
        assert record_dict["sequence_number"] == 1
        assert record_dict["previous_hash"] == GENESIS_HASH
        assert record_dict["record_hash"] == record.record_hash
        assert "event" in record_dict
        assert json.dumps(record_dict)  # Should not raise


class TestInMemoryAuditStore:
    """Tests for InMemoryAuditStore."""

    def test_append_and_get_last(self) -> None:
        """Appending records allows retrieval of last record."""
        store = InMemoryAuditStore()
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.read",
            decision="allow",
        )
        record = SealedAuditRecord.seal(event, sequence_number=1, previous_hash=GENESIS_HASH)

        store.append(record)
        last = store.get_last("phlo-api")

        assert last is not None
        assert last.sequence_number == 1
        assert last.record_hash == record.record_hash

    def test_get_last_returns_none_for_empty_surface(self) -> None:
        """Getting last record for empty surface returns None."""
        store = InMemoryAuditStore()

        assert store.get_last("nonexistent") is None

    def test_query_returns_filtered_records(self) -> None:
        """Query returns records within bounds."""
        store = InMemoryAuditStore()

        for i in range(1, 6):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject=f"user{i}@example.com",
                action="dataset.read",
                decision="allow",
            )
            record = SealedAuditRecord.seal(event, sequence_number=i, previous_hash=GENESIS_HASH)
            store.append(record)

        results = store.query("phlo-api", after=2, before=5, limit=10)

        assert len(results) == 2
        assert results[0].sequence_number == 3
        assert results[1].sequence_number == 4

    def test_verify_chain_passes_for_valid_chain(self) -> None:
        """verify_chain passes for a valid chain."""
        store = InMemoryAuditStore()

        for i in range(1, 4):
            prev = store.get_last("phlo-api")
            prev_hash = prev.record_hash if prev else GENESIS_HASH

            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject=f"user{i}@example.com",
                action="dataset.read",
                decision="allow",
            )
            record = SealedAuditRecord.seal(event, sequence_number=i, previous_hash=prev_hash)
            store.append(record)

        result = store.verify_chain("phlo-api")

        assert result.valid is True
        assert result.total_records == 3
        assert result.first_invalid_sequence is None

    def test_verify_chain_fails_on_resealed_tampered_record(self) -> None:
        """verify_chain fails when a tampered record breaks downstream links."""
        store = InMemoryAuditStore()

        for i in range(1, 4):
            prev = store.get_last("phlo-api")
            prev_hash = prev.record_hash if prev else GENESIS_HASH

            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject=f"user{i}@example.com",
                action="dataset.read",
                decision="allow",
            )
            record = SealedAuditRecord.seal(event, sequence_number=i, previous_hash=prev_hash)
            store.append(record)

        records = store.query("phlo-api", limit=100)
        records[1] = SealedAuditRecord.seal(
            CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject="TAMPERED",
                action="dataset.read",
                decision="allow",
            ),
            sequence_number=2,
            previous_hash=records[0].record_hash,
        )

        store._records["phlo-api"] = records

        result = store.verify_chain("phlo-api")

        assert result.valid is False
        assert result.first_invalid_sequence == 3
        assert "mismatch" in result.error_message.lower()

    def test_verify_chain_fails_when_event_data_changes_without_rehash(self) -> None:
        """verify_chain fails immediately if event data changes without updating the HMAC."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        for i in range(1, 4):
            sink.write(
                CanonicalAuditEvent(
                    event_type="authorization",
                    surface="phlo-api",
                    actor_subject=f"user{i}@example.com",
                    action="dataset.read",
                    decision="allow",
                )
            )

        records = store.query("phlo-api", limit=100)
        tampered = CanonicalAuditEvent(
            event_type=records[1].event.event_type,
            surface=records[1].event.surface,
            actor_subject="TAMPERED",
            action=records[1].event.action,
            decision=records[1].event.decision,
        )
        records[1] = SealedAuditRecord(
            sequence_number=records[1].sequence_number,
            sealed_at=records[1].sealed_at,
            previous_hash=records[1].previous_hash,
            record_hash=records[1].record_hash,
            event=tampered,
        )
        store._records["phlo-api"] = records

        result = store.verify_chain("phlo-api")

        assert result.valid is False
        assert result.first_invalid_sequence == 2
        assert result.error_message == "Record hash mismatch at sequence 2"


class TestTamperEvidentAuditSink:
    """Tests for TamperEvidentAuditSink."""

    def test_write_increments_sequence(self) -> None:
        """Writing events increments sequence numbers."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        for i in range(1, 4):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject=f"user{i}@example.com",
                action="dataset.read",
                decision="allow",
            )
            sink.write(event)

        last = store.get_last("phlo-api")
        assert last is not None
        assert last.sequence_number == 3

    def test_write_maintains_chain(self) -> None:
        """Written events maintain proper hash chain."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        events = []
        for i in range(1, 4):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject=f"user{i}@example.com",
                action="dataset.read",
                decision="allow",
            )
            events.append(event)
            sink.write(event)

        result = store.verify_chain("phlo-api")
        assert result.valid is True
        assert result.total_records == 3

    def test_write_creates_independent_surface_chains(self) -> None:
        """Different surfaces get independent chains."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        for surface in ["phlo-api", "dagster"]:
            for i in range(1, 4):
                event = CanonicalAuditEvent(
                    event_type="authorization",
                    surface=surface,
                    actor_subject=f"user{i}@example.com",
                    action="dataset.read",
                    decision="allow",
                )
                sink.write(event)

        for surface in ["phlo-api", "dagster"]:
            result = store.verify_chain(surface)
            assert result.valid is True
            assert result.total_records == 3
