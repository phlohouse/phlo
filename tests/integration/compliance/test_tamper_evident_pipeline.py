"""Integration tests for tamper-evident audit pipeline.

Verifies the full tamper-evident audit chain:
- Events are sealed with hash chaining
- Chain verification passes for valid chain
- Chain verification fails when records are tampered
- Each surface gets independent chain
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.audit.events import CanonicalAuditEvent
from phlo.compliance.audit import InMemoryAuditStore, TamperEvidentAuditSink

pytestmark = pytest.mark.integration


class TestTamperEvidentPipeline:
    """Integration tests for tamper-evident audit chain."""

    def test_full_pipeline_events_sealed_with_chain(self) -> None:
        """Events are sealed with correct hash chain."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        events = []
        for i in range(5):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface=f"test-surface-{i % 2}",
                action="test.action",
                resource_type="test",
                resource_id=f"resource-{i}",
                actor_subject=f"user-{i}@example.com",
                actor_type="user",
                actor_roles=("role_a",),
                authentication_source="test",
                decision="allow",
                reason_code="",
                policy_id=None,
                request_id=f"req-{i}",
            )
            sink.write(event)
            events.append(event)

        for surface in ["test-surface-0", "test-surface-1"]:
            surface_records = store.query(surface, limit=100)
            assert len(surface_records) >= 2, f"Expected at least 2 records for {surface}"

            for i, record in enumerate(surface_records):
                assert record.record_hash != "", f"Record {i} should be sealed"
                assert record.sequence_number == i + 1, f"Record {i} should have sequence {i + 1}"

                if i == 0:
                    assert record.previous_hash == "0" * 64, "First record should have genesis hash"
                else:
                    assert record.previous_hash == surface_records[i - 1].record_hash, (
                        f"Record {i} should link to previous hash"
                    )

    def test_verify_chain_passes_for_valid_chain(self) -> None:
        """Chain verification passes for unmodified chain."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        surface = "test-surface"
        for i in range(10):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface=surface,
                action="dataset.read",
                resource_type="dataset",
                resource_id=f"dataset-{i}",
                actor_subject="alice@example.com",
                actor_type="user",
                actor_roles=("data_read",),
                authentication_source="proxy",
                decision="allow",
                reason_code="",
                policy_id=None,
                request_id=f"req-{i}",
            )
            sink.write(event)

        result = store.verify_chain(surface)
        assert result.valid is True
        assert result.first_invalid_sequence is None

    def test_verify_chain_fails_on_tampered_record(self) -> None:
        """Chain verification fails when a record is tampered."""
        from dataclasses import replace

        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        surface = "test-surface"
        for i in range(5):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface=surface,
                action="dataset.write",
                resource_type="dataset",
                resource_id=f"dataset-{i}",
                actor_subject="alice@example.com",
                actor_type="user",
                actor_roles=("data_write",),
                authentication_source="proxy",
                decision="allow",
                reason_code="",
                policy_id=None,
                request_id=f"req-{i}",
            )
            sink.write(event)

        records = store.query(surface, limit=100)
        assert len(records) == 5

        tampered_record = replace(records[2], previous_hash="tampered_hash_" + "0" * 48)
        store._records[surface][2] = tampered_record

        result = store.verify_chain(surface)
        assert result.valid is False
        assert result.first_invalid_sequence == 3

    def test_independent_chains_per_surface(self) -> None:
        """Each surface maintains an independent chain."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        surfaces = ["surface-a", "surface-b", "surface-c"]
        for i in range(10):
            for surface in surfaces:
                event = CanonicalAuditEvent(
                    event_type="authorization",
                    surface=surface,
                    action=f"action.{i}",
                    resource_type="resource",
                    resource_id=f"resource-{i}",
                    actor_subject="user@example.com",
                    actor_type="user",
                    actor_roles=(),
                    authentication_source="test",
                    decision="allow",
                    reason_code="",
                    policy_id=None,
                    request_id=f"req-{surface}-{i}",
                )
                sink.write(event)

        for surface in surfaces:
            records = store.query(surface, limit=100)
            assert len(records) == 10, f"Expected 10 records for {surface}, got {len(records)}"

            for i, record in enumerate(records):
                assert record.sequence_number == i + 1, f"Sequence should be {i + 1} for {surface}"
                if i == 0:
                    assert record.previous_hash == "0" * 64, "First record should have genesis hash"
                else:
                    assert record.previous_hash == records[i - 1].record_hash, (
                        f"Chain broken at {i} for {surface}"
                    )

    def test_verify_and_export_workflow(self, tmp_path) -> None:
        """Chain verification and export workflow works correctly."""
        from phlo.compliance.audit.export import verify_and_export

        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        surface = "test-surface"
        for i in range(5):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface=surface,
                action="config.update",
                resource_type="config",
                resource_id=f"config-{i}",
                actor_subject="admin@example.com",
                actor_type="user",
                actor_roles=("admin",),
                authentication_source="jwt",
                decision="allow",
                reason_code="",
                policy_id="policy-1",
                request_id=f"req-{i}",
            )
            sink.write(event)

        output_dir = Path(tmp_path)
        result = verify_and_export(store, surface, output_dir)

        assert result["record_count"] == 5
        assert result["verification"].valid is True

    def test_regulated_mode_enables_tamper_evident_sink(self) -> None:
        """In regulated mode, tamper-evident sink is used."""
        from phlo.compliance.features import resolve_compliance_features

        features = resolve_compliance_features(regulated=True)
        assert features.tamper_evident_audit is True

        features_no_regulated = resolve_compliance_features(regulated=False)
        assert features_no_regulated.tamper_evident_audit is False

    def test_compliance_features_resolve_correctly(self) -> None:
        """ComplianceFeatures resolve correctly for regulated vs non-regulated."""
        from phlo.compliance.features import ComplianceFeatures

        regulated_features = ComplianceFeatures(
            tamper_evident_audit=True,
            electronic_signatures=True,
            system_manifest=True,
            access_governance=True,
            evidence_export=True,
        )

        assert regulated_features.tamper_evident_audit is True
        assert regulated_features.electronic_signatures is True
        assert regulated_features.system_manifest is True
        assert regulated_features.access_governance is True
        assert regulated_features.evidence_export is True

        open_features = ComplianceFeatures()
        assert open_features.tamper_evident_audit is False
        assert open_features.electronic_signatures is False
        assert open_features.system_manifest is False
        assert open_features.access_governance is False
        assert open_features.evidence_export is False
