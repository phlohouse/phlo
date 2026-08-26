"""Integration tests for the compliance plane.

Chains audit events, signatures, governance, and evidence packs through full
workflow scenarios using temporary directories.
"""

from __future__ import annotations

from pathlib import Path

from phlo.audit.events import CanonicalAuditEvent
from phlo.compliance.audit import (
    InMemoryAuditStore,
    TamperEvidentAuditSink,
)
from phlo.compliance.evidence import (
    create_evidence_pack,
    verify_evidence_pack,
)
from phlo.compliance.governance import (
    DEFAULT_SOD_POLICIES,
    SeparationOfDutiesPolicy,
    check_separation_of_duties,
)
from phlo.compliance.manifest import (
    ComplianceMode,
    ComponentVersion,
    DeploymentEnvironment,
    SecurityConfiguration,
    capture_manifest,
)
from phlo.compliance.signatures import (
    SignatureMeaning,
    SignatureRequest,
    SignatureService,
    SignatureServiceConfig,
)

_TEST_KEY = b"test-evidence-hmac-key-0123456789"
_WRONG_KEY = b"wrong-evidence-hmac-key-9876543210"


class TestComplianceManifestIntegration:
    """Integration test: manifest captures compliance features."""

    def test_full_manifest_with_compliance_features(self) -> None:
        """System manifest captures full compliance state."""
        security = SecurityConfiguration(
            compliance_mode=ComplianceMode.REGULATED,
            regulated=True,
            tamper_evident_audit=True,
            electronic_signatures=True,
            access_governance=True,
            auth_providers=("jwt", "oidc"),
            require_mfa=True,
            session_timeout_seconds=3600,
        )
        components = [
            ComponentVersion(
                name="phlo-api",
                version="1.0.0",
                build_hash="abc123",
                deploy_timestamp="2024-01-01T00:00:00Z",
            ),
            ComponentVersion(
                name="phlo-dagster",
                version="1.0.0",
            ),
        ]
        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=security,
            components=components,
            config_snapshot={"log_level": "INFO"},
            platform="kubernetes",
            region="us-east-1",
        )

        assert manifest.security.compliance_mode == ComplianceMode.REGULATED
        assert manifest.security.tamper_evident_audit is True
        assert manifest.security.electronic_signatures is True
        assert len(manifest.components) == 2


class TestAuditChainIntegration:
    """Integration test: audit chain with evidence export."""

    def test_audit_chain_export_and_verify(self) -> None:
        """Sealed audit records can be exported and verified."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        from phlo.audit.events import CanonicalAuditEvent

        for i in range(5):
            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
                actor_subject=f"user{i}@example.com",
                action="dataset.read",
                decision="allow",
            )
            sink.write(event)

        verification = store.verify_chain("phlo-api")
        assert verification.valid is True
        assert verification.total_records == 5

        records = store.query("phlo-api", limit=100)
        assert len(records) == 5


class TestSignatureIntegration:
    """Integration test: signature workflow with audit."""

    def test_signature_produces_audit_event(self) -> None:
        """Signatures are recorded and produce audit events."""
        events_emitted: list[CanonicalAuditEvent] = []

        def capture_event(event: CanonicalAuditEvent) -> None:
            events_emitted.append(event)

        class MockAuditEmitter:
            def emit(self, event: CanonicalAuditEvent) -> None:
                capture_event(event)

        service = SignatureService(
            config=SignatureServiceConfig(critical_actions=frozenset(["dataset.publish"])),
            audit_emitter=MockAuditEmitter(),
        )

        from phlo.capabilities.interfaces import AuthenticatedSession, AuthPrincipal

        session = AuthenticatedSession(
            principal=AuthPrincipal(subject="alice@example.com", principal_type="user"),
            auth_method="oidc",
            provider_name="test",
        )

        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="v1",
            justification="Approved for release",
        )

        record = service.sign(request, session)
        assert record.signature_hash != ""

        assert len(events_emitted) == 1
        assert events_emitted[0].event_type == "signature"


class TestGovernanceIntegration:
    """Integration test: governance policies with audit."""

    def test_separation_of_duties_detection(self) -> None:
        """SoD violations are detected in role assignments."""
        policy = SeparationOfDutiesPolicy(
            policy_id="sod-test",
            description="Test policy",
            conflicting_roles=("role_a", "role_b"),
        )

        no_violation = policy.check_violation(("role_a", "role_c"))
        assert no_violation is None

        violation = policy.check_violation(("role_a", "role_b"))
        assert violation is not None
        assert "role_a" in violation.conflicting_roles
        assert "role_b" in violation.conflicting_roles

    def test_check_separation_of_duties_with_policies(self) -> None:
        """check_separation_of_duties uses provided policies."""
        violations = check_separation_of_duties(
            principal_subject="user@example.com",
            roles=("payment_approver", "payment_initiator"),
            policies=DEFAULT_SOD_POLICIES,
        )

        assert len(violations) > 0
        assert any("payment" in v.policy_id.lower() for v in violations)


class TestEvidencePackIntegration:
    """Integration test: evidence pack creation and verification."""

    def test_evidence_pack_roundtrip(self, tmp_path) -> None:
        """Evidence pack can be created, written, and verified."""
        pack = create_evidence_pack(
            created_by="test@example.com",
            compliance_domain="test",
            description="Test evidence pack",
            audit_records=[
                {"event_type": "authorization", "actor": "user@example.com"},
                {"event_type": "signature", "signer": "admin@example.com"},
            ],
            signatures=[
                {"signature_id": "sig-1", "meaning": "approved"},
            ],
            manifest_data={"version": "1.0.0"},
            hmac_key=_TEST_KEY,
        )

        assert pack.manifest.file_count == 3
        assert pack.manifest.record_count == 2

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        assert zip_path.exists()

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is True
        assert verification["record_count"] == 2

    def test_verify_detects_tampering(self, tmp_path) -> None:
        """Verification detects tampered evidence packs."""
        pack = create_evidence_pack(
            created_by="test@example.com",
            audit_records=[{"test": "data"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        import zipfile

        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr("extra_file.txt", b"tampered content")

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is False
        assert "Unexpected files" in verification.get("error", "")

    # ------------------------------------------------------------------
    # Format v2: HMAC-SHA256 authenticated integrity
    # ------------------------------------------------------------------

    def test_evidence_pack_signature_roundtrip(self, tmp_path) -> None:
        """A newly exported pack verifies with the same explicit key."""
        pack = create_evidence_pack(
            created_by="test@example.com",
            audit_records=[{"event": "test"}],
            signatures=[{"signer": "alice@example.com"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        result = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert result["valid"] is True
        assert result["format_version"] == 2

    def test_evidence_pack_signature_wrong_key(self, tmp_path) -> None:
        """Verification fails with a different key."""
        pack = create_evidence_pack(
            created_by="test@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        result = verify_evidence_pack(zip_path, hmac_key=_WRONG_KEY)
        assert result["valid"] is False

    def test_evidence_pack_signature_recomputed_checksums(self, tmp_path) -> None:
        """Verification fails after attacker recomputes all checksums."""
        import hashlib
        import json
        import zipfile

        pack = create_evidence_pack(
            created_by="test@example.com",
            audit_records=[{"event": "original"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        # Attacker rewrites evidence and recomputes checksums.json
        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest_bytes = zf.read("manifest.json")
            sig_bytes = zf.read("signature.json")
            files = {
                name: zf.read(name)
                for name in zf.namelist()
                if name not in ("manifest.json", "checksums.json", "signature.json")
            }

        files["audit_records.jsonl"] = json.dumps({"event": "fabricated"}, sort_keys=True).encode()

        recomputed = json.dumps(
            {
                "manifest_hash": hashlib.sha256(manifest_bytes).hexdigest(),
                "files": {
                    name: hashlib.sha256(content).hexdigest() for name, content in files.items()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

        attacked = Path(tmp_path) / "attacked.zip"
        with zipfile.ZipFile(attacked, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_bytes)
            for name, content in files.items():
                zf.writestr(name, content)
            zf.writestr("checksums.json", recomputed)
            zf.writestr("signature.json", sig_bytes)

        result = verify_evidence_pack(attacked, hmac_key=_TEST_KEY)
        assert result["valid"] is False


class TestFullComplianceWorkflow:
    """End-to-end compliance workflow integration test."""

    def test_regulated_deployment_workflow(self, tmp_path) -> None:
        """Complete workflow from manifest to evidence pack."""
        security = SecurityConfiguration(
            compliance_mode=ComplianceMode.REGULATED,
            regulated=True,
            tamper_evident_audit=True,
            electronic_signatures=True,
            access_governance=True,
        )

        manifest = capture_manifest(
            phlo_version="1.0.0",
            environment=DeploymentEnvironment.PRODUCTION,
            security=security,
        )

        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        from phlo.audit.events import CanonicalAuditEvent

        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="phlo-api",
            actor_subject="alice@example.com",
            action="dataset.publish",
            decision="allow",
        )
        sink.write(event)

        verification = store.verify_chain("phlo-api")
        assert verification.valid is True

        records = store.query("phlo-api", limit=100)
        record_dicts = [r.to_dict() for r in records]

        evidence = create_evidence_pack(
            created_by="system",
            compliance_domain="regulated",
            description="Production regulated deployment evidence",
            audit_records=record_dicts,
            manifest_data={
                "manifest_id": manifest.manifest_id,
                "phlo_version": manifest.phlo_version,
                "environment": manifest.environment,
            },
            hmac_key=_TEST_KEY,
        )

        assert evidence.manifest.compliance_domain == "regulated"

        zip_path = Path(tmp_path) / "evidence.zip"
        evidence.write_zip(zip_path)

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is True
