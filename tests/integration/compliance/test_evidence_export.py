"""Integration tests for evidence export.

Verifies:
- Evidence pack creation with manifest
- Verification detects tampering
- Pack includes audit chain, signatures, manifest
- CLI export-evidence integration
- HMAC-SHA256 authenticated integrity (format v2)
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from phlo.compliance.audit import InMemoryAuditStore, TamperEvidentAuditSink
from phlo.compliance.evidence import (
    EVIDENCE_PACK_ALGORITHM,
    EVIDENCE_PACK_FORMAT_VERSION,
    EvidenceKeyError,
    create_evidence_pack,
    verify_evidence_pack,
)
from phlo.compliance.manifest import (
    ComplianceMode,
    DeploymentEnvironment,
    capture_manifest,
)

pytestmark = pytest.mark.integration

_TEST_KEY = b"test-evidence-hmac-key-0123456789"
_WRONG_KEY = b"wrong-evidence-hmac-key-9876543210"


def _rebuild_zip(zip_path: Path, replacement: dict[str, bytes]) -> Path:
    """Rewrite a ZIP, replacing entries from *replacement* and keeping the rest."""
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            entries[name] = zf.read(name)
    entries.update(replacement)
    out = zip_path.parent / "rebuilt.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return out


class TestEvidenceExport:
    """Integration tests for evidence pack export."""

    def test_evidence_pack_roundtrip(self, tmp_path) -> None:
        """Evidence pack can be created, written, and verified."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            compliance_domain="sox",
            description="Q4 access review evidence",
            audit_records=[{"event": "test", "data": "value"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        assert zip_path.exists()

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is True
        assert verification["pack_id"] == pack.manifest.pack_id

    def test_evidence_pack_includes_audit_records(self, tmp_path) -> None:
        """Evidence pack includes audit records in export."""
        audit_records = []
        for i in range(10):
            audit_records.append(
                {
                    "event_type": "authorization",
                    "surface": "test-surface",
                    "action": f"action.{i}",
                    "actor_subject": f"user-{i}@example.com",
                    "decision": "allow",
                }
            )

        pack = create_evidence_pack(
            created_by="compliance@example.com",
            compliance_domain="hipaa",
            description="Access certification evidence",
            audit_records=audit_records,
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            content = zf.read("audit_records.jsonl")
            lines = content.decode().strip().split("\n")
            assert len(lines) == 10

    def test_verify_detects_extra_files(self, tmp_path) -> None:
        """Verification fails when extra files are added to pack."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"test": "data"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr("extra_file.txt", b"tampered content")

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is False
        assert "Unexpected files" in verification.get("error", "")

    def test_verify_detects_missing_files(self, tmp_path) -> None:
        """Verification fails when expected files are missing."""
        zip_path = Path(tmp_path) / "evidence.zip"

        # Manually create a v1-style pack (no signature.json)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            manifest_json = json.dumps(
                {
                    "pack_id": "test-id",
                    "created_at": "2024-01-01T00:00:00Z",
                    "created_by": "admin@example.com",
                    "compliance_domain": "sox",
                    "description": "Test",
                    "record_count": 1,
                    "file_count": 1,
                    "total_size_bytes": 100,
                    "sha256_hash": "abc123",
                }
            )
            zf.writestr("manifest.json", manifest_json)

            checksums_json = json.dumps(
                {
                    "manifest_hash": "abc123",
                    "files": {
                        "audit_records.jsonl": "hash123",
                    },
                }
            )
            zf.writestr("checksums.json", checksums_json)

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is False
        assert verification.get("format_version") == 1

    def test_evidence_pack_with_system_manifest(self, tmp_path) -> None:
        """Evidence pack includes system manifest data."""
        from phlo.compliance.manifest import SecurityConfiguration

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

        manifest_data = {
            "manifest_id": manifest.manifest_id,
            "environment": manifest.environment.value,
            "components": [
                {
                    "name": c.name,
                    "version": c.version,
                    "build_hash": c.build_hash,
                    "deploy_timestamp": c.deploy_timestamp,
                }
                for c in manifest.components
            ],
        }

        pack = create_evidence_pack(
            created_by="system@example.com",
            compliance_domain="sox",
            description="System manifest evidence",
            manifest_data=manifest_data,
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest_content = zf.read("system_manifest.json")
            loaded = json.loads(manifest_content)
            assert "manifest_id" in loaded
            assert loaded["environment"] == "production"

    def test_evidence_pack_with_signature_records(self, tmp_path) -> None:
        """Evidence pack includes signature records."""
        sig_records = [
            {
                "signer_subject": "alice@example.com",
                "meaning": "approved",
                "record_type": "dataset",
                "record_id": f"dataset-{i}",
                "signature_hash": f"hash-{i}",
            }
            for i in range(5)
        ]

        pack = create_evidence_pack(
            created_by="compliance@example.com",
            signatures=sig_records,
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            sigs_content = zf.read("signatures.jsonl")
            lines = sigs_content.decode().strip().split("\n")
            assert len(lines) == 5

    def test_cli_export_evidence_command(self, monkeypatch, tmp_path) -> None:
        """CLI export-evidence command creates valid pack."""
        from click.testing import CliRunner

        from phlo.cli.commands.compliance import export_evidence

        monkeypatch.setenv("PHLO_EVIDENCE_HMAC_KEY", _TEST_KEY.decode())
        runner = CliRunner()

        output_path = Path(tmp_path) / "evidence.zip"

        result = runner.invoke(
            export_evidence,
            [
                "--output",
                str(output_path),
                "--created-by",
                "cli-user@example.com",
                "--domain",
                "pci",
                "--description",
                "PCI compliance evidence",
            ],
        )

        assert result.exit_code == 0
        assert output_path.exists()

        verification = verify_evidence_pack(output_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is True

    def test_cli_verify_evidence_command(self, monkeypatch, tmp_path) -> None:
        """CLI verify-evidence command validates packs correctly."""
        from click.testing import CliRunner

        from phlo.cli.commands.compliance import verify_evidence

        monkeypatch.setenv("PHLO_EVIDENCE_HMAC_KEY", _TEST_KEY.decode())
        pack = create_evidence_pack(
            created_by="admin@example.com",
            hmac_key=_TEST_KEY,
        )

        runner = CliRunner()

        output_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(output_path)

        result = runner.invoke(
            verify_evidence,
            [str(output_path)],
        )

        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_evidence_pack_contains_correct_metadata(self) -> None:
        """Evidence pack contains correct metadata from inputs."""
        audit_records = [{"event": "test", "data": "value"}]

        pack = create_evidence_pack(
            created_by="admin@example.com",
            compliance_domain="sox",
            description="Test evidence pack",
            audit_records=audit_records,
        )

        assert pack.manifest.created_by == "admin@example.com"
        assert pack.manifest.compliance_domain == "sox"
        assert pack.manifest.description == "Test evidence pack"
        assert pack.manifest.record_count == 1
        assert pack.manifest.file_count > 0

    def test_full_compliance_pipeline_integration(self, tmp_path) -> None:
        """End-to-end: create audit events, sign, export, verify."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        for i in range(5):
            from phlo.audit.events import CanonicalAuditEvent

            event = CanonicalAuditEvent(
                event_type="authorization",
                surface="phlo-api",
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

        audit_records = []
        for record in store.query("phlo-api", limit=100):
            audit_records.append(record.event.to_dict())

        pack = create_evidence_pack(
            created_by="compliance@example.com",
            compliance_domain="sox",
            description="Full pipeline test",
            audit_records=audit_records,
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "full_evidence.zip"
        pack.write_zip(zip_path)

        verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert verification["valid"] is True
        assert verification["record_count"] == 5

    def test_evidence_pack_multiple_compliance_domains(self, tmp_path) -> None:
        """Evidence packs can specify different compliance domains."""
        for domain in ["sox", "hipaa", "pci", "gdpr"]:
            pack = create_evidence_pack(
                created_by="admin@example.com",
                compliance_domain=domain,
                hmac_key=_TEST_KEY,
            )

            zip_path = Path(tmp_path) / f"evidence_{domain}.zip"
            pack.write_zip(zip_path)

            verification = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
            assert verification["valid"] is True

    # ------------------------------------------------------------------
    # Format v2: HMAC-SHA256 authenticated integrity
    # ------------------------------------------------------------------

    def test_evidence_pack_signature_roundtrip(self, tmp_path) -> None:
        """A newly exported pack verifies with the same explicit key."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test", "data": "value"}],
            signatures=[{"signer": "alice@example.com"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        result = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert result["valid"] is True
        assert result["format_version"] == EVIDENCE_PACK_FORMAT_VERSION
        assert result["key_id"]

        # signature.json is present and has the expected structure
        with zipfile.ZipFile(zip_path, "r") as zf:
            sig = json.loads(zf.read("signature.json"))
        assert sig["version"] == EVIDENCE_PACK_FORMAT_VERSION
        assert sig["algorithm"] == EVIDENCE_PACK_ALGORITHM
        assert sig["key_id"] == result["key_id"]
        assert "checksum_envelope_digest" in sig
        assert "authentication_value" in sig
        # The key itself is never stored
        assert _TEST_KEY.decode() not in json.dumps(sig)

    def test_evidence_pack_signature_wrong_key(self, tmp_path) -> None:
        """Verification fails with a different key."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        result = verify_evidence_pack(zip_path, hmac_key=_WRONG_KEY)
        assert result["valid"] is False
        assert "Key identifier mismatch" in result["error"]

    def test_evidence_pack_signature_modified_evidence(self, tmp_path) -> None:
        """Verification fails when any evidence file changes."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "original", "data": "value"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        # Replace audit_records.jsonl with tampered content
        tampered = _rebuild_zip(
            zip_path,
            {"audit_records.jsonl": json.dumps({"event": "tampered"}, sort_keys=True).encode()},
        )

        result = verify_evidence_pack(tampered, hmac_key=_TEST_KEY)
        assert result["valid"] is False
        # The checksums.json still references the old hash, so the
        # internal hash check or the envelope digest check catches it.
        assert result["error"]

    def test_evidence_pack_signature_modified_manifest(self, tmp_path) -> None:
        """Verification fails when the manifest changes."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
        manifest["created_by"] = "attacker@example.com"
        tampered = _rebuild_zip(
            zip_path,
            {"manifest.json": json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()},
        )

        result = verify_evidence_pack(tampered, hmac_key=_TEST_KEY)
        assert result["valid"] is False
        assert result["error"]

    def test_evidence_pack_signature_recomputed_checksums_attack(self, tmp_path) -> None:
        """Adversarial: rewrite audit_records.jsonl, recompute all of
        checksums.json without the key, and assert verification fails.

        This is the exact attack required by the issue: an attacker with
        archive access modifies evidence and rebuilds every checksum entry
        so the internal hash check would pass — but the HMAC signature
        over the canonical checksums.json bytes cannot be recomputed
        without the external key.
        """
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "original", "seq": 1}],
            signatures=[{"signer": "alice@example.com"}],
            manifest_data={"version": "1.0.0"},
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        # --- attacker modifies audit_records.jsonl ---
        tampered_records = json.dumps({"event": "fabricated", "seq": 999}, sort_keys=True).encode()

        # --- attacker recomputes checksums.json for ALL changed files ---
        with zipfile.ZipFile(zip_path, "r") as zf:
            manifest_bytes = zf.read("manifest.json")
            sig_bytes = zf.read("signature.json")
            other_files = {
                name: zf.read(name)
                for name in zf.namelist()
                if name not in ("manifest.json", "checksums.json", "signature.json")
            }

        new_files = dict(other_files)
        new_files["audit_records.jsonl"] = tampered_records

        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        recomputed_checksums = json.dumps(
            {
                "manifest_hash": manifest_hash,
                "files": {
                    name: hashlib.sha256(content).hexdigest() for name, content in new_files.items()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

        # Attacker rebuilds the archive with tampered evidence and
        # recomputed checksums, keeping the original signature.json.
        rebuilt = Path(tmp_path) / "attacked.zip"
        with zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_bytes)
            for name, content in new_files.items():
                zf.writestr(name, content)
            zf.writestr("checksums.json", recomputed_checksums)
            zf.writestr("signature.json", sig_bytes)

        # Verification must fail despite internally consistent checksums.
        result = verify_evidence_pack(rebuilt, hmac_key=_TEST_KEY)
        assert result["valid"] is False
        # The envelope digest in signature.json no longer matches the
        # recomputed checksums.json, and the HMAC cannot match either.
        assert result["error"]

    def test_evidence_pack_signature_missing(self, tmp_path) -> None:
        """Verification fails if signature.json is missing (unsigned v1)."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        # Remove signature.json
        entries: dict[str, bytes] = {}
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name != "signature.json":
                    entries[name] = zf.read(name)
        stripped = zip_path.parent / "unsigned.zip"
        with zipfile.ZipFile(stripped, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in entries.items():
                zf.writestr(name, content)

        result = verify_evidence_pack(stripped, hmac_key=_TEST_KEY)
        assert result["valid"] is False
        assert result.get("format_version") == 1
        assert "unsigned" in result["error"].lower()

    def test_evidence_pack_signature_changed(self, tmp_path) -> None:
        """Verification fails if signature.json is changed."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            sig = json.loads(zf.read("signature.json"))
        # Tamper the authentication value
        sig["authentication_value"] = "a" * 64
        tampered = _rebuild_zip(
            zip_path,
            {"signature.json": json.dumps(sig, sort_keys=True, separators=(",", ":")).encode()},
        )

        result = verify_evidence_pack(tampered, hmac_key=_TEST_KEY)
        assert result["valid"] is False
        assert result["error"]

    def test_evidence_pack_export_no_key_fails_closed(self, monkeypatch, tmp_path) -> None:
        """Export fails when no key material is available."""
        monkeypatch.delenv("PHLO_EVIDENCE_HMAC_KEY", raising=False)
        monkeypatch.delenv("PHLO_AUDIT_HMAC_KEY", raising=False)

        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        with pytest.raises(EvidenceKeyError, match="No evidence-pack key material"):
            pack.write_zip(zip_path)

        assert not zip_path.exists()

    def test_evidence_pack_verify_no_key_fails_closed(self, monkeypatch, tmp_path) -> None:
        """Verification fails closed when no key material is available."""
        monkeypatch.delenv("PHLO_EVIDENCE_HMAC_KEY", raising=False)
        monkeypatch.delenv("PHLO_AUDIT_HMAC_KEY", raising=False)

        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        result = verify_evidence_pack(zip_path)
        assert result["valid"] is False
        assert "key material" in result["error"].lower()

    def test_evidence_pack_signature_no_secret_exposure(self, tmp_path) -> None:
        """Results and logs expose neither key material nor expected auth value."""
        pack = create_evidence_pack(
            created_by="admin@example.com",
            audit_records=[{"event": "test"}],
            hmac_key=_TEST_KEY,
        )

        zip_path = Path(tmp_path) / "evidence.zip"
        pack.write_zip(zip_path)

        # Positive verification
        result = verify_evidence_pack(zip_path, hmac_key=_TEST_KEY)
        assert result["valid"] is True
        result_json = json.dumps(result)
        assert _TEST_KEY.decode() not in result_json
        assert "authentication_value" not in result_json

        # Negative verification (wrong key)
        wrong_result = verify_evidence_pack(zip_path, hmac_key=_WRONG_KEY)
        wrong_json = json.dumps(wrong_result)
        assert _WRONG_KEY.decode() not in wrong_json
        assert _TEST_KEY.decode() not in wrong_json
        assert "authentication_value" not in wrong_json

        # The pack repr must not include the key
        pack_repr = repr(pack)
        assert _TEST_KEY.decode() not in pack_repr
