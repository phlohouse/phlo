"""Evidence pack creation for compliance auditing.

Evidence packs bundle audit records, signatures, manifests, and other
compliance artifacts into a tamper-evident package suitable for
submission to auditors or regulators.

Format version 2 authenticates the canonical ``checksums.json`` bytes
with HMAC-SHA256 using explicit key material held outside the archive.
A ``signature.json`` file records the pack format version, algorithm,
key identifier, checksum-envelope digest, and authentication value.
The key itself is never stored in the pack.  Verification rejects
unsigned version-1 packs, wrong keys, modified evidence, modified
manifests, recomputed checksums, and missing or changed signatures.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

EVIDENCE_PACK_FORMAT_VERSION = 2
# Current evidence-pack format version.

EVIDENCE_PACK_ALGORITHM = "HMAC-SHA256"
# Authentication algorithm used by evidence-pack format v2.

PHLO_EVIDENCE_HMAC_KEY_ENV = "PHLO_EVIDENCE_HMAC_KEY"
# Preferred environment variable for the evidence-pack HMAC key.

PHLO_AUDIT_HMAC_KEY_ENV = "PHLO_AUDIT_HMAC_KEY"
# Fallback environment variable, reusing the regulated audit-key contract.

# Files inside the archive that are not evidence payloads.
_ARCHIVE_META_FILES = frozenset({"manifest.json", "checksums.json", "signature.json"})


class EvidenceKeyError(RuntimeError):
    """Raised when key material is required but not available."""


# ---------------------------------------------------------------------------
# Key resolution and cryptographic helpers
# ---------------------------------------------------------------------------


def _resolve_evidence_hmac_key(explicit: bytes | None = None) -> bytes:
    """Return the HMAC key for evidence-pack authentication.

    Precedence:
        1. *explicit* argument passed by the caller.
        2. ``PHLO_EVIDENCE_HMAC_KEY`` environment variable.
        3. ``PHLO_AUDIT_HMAC_KEY`` environment variable (audit-key contract).

    No development default is generated.  When no key material is available
    an :class:`EvidenceKeyError` is raised so that export and verification
    fail closed rather than silently degrading to an unsigned pack.
    """
    if explicit is not None:
        if not explicit:
            raise EvidenceKeyError("Empty key material provided for evidence pack")
        return explicit
    env_key = os.environ.get(PHLO_EVIDENCE_HMAC_KEY_ENV) or os.environ.get(PHLO_AUDIT_HMAC_KEY_ENV)
    if env_key:
        return env_key.encode()
    raise EvidenceKeyError(
        f"No evidence-pack key material: set {PHLO_EVIDENCE_HMAC_KEY_ENV}"
        f" or {PHLO_AUDIT_HMAC_KEY_ENV}"
    )


def _compute_key_id(key: bytes) -> str:
    """Return a non-reversible identifier for *key*.

    The identifier is the first 16 hex characters of ``SHA-256(key)``.
    It lets a verifier detect a wrong-key situation without exposing the
    key itself or the expected authentication value.
    """
    return hashlib.sha256(key).hexdigest()[:16]


def _canonical_json_bytes(data: Any) -> bytes:
    """Serialise *data* to deterministic, compact JSON bytes."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, kw_only=True)
class EvidenceManifest:
    """Manifest describing the contents of an evidence pack."""

    pack_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_by: str
    pack_format_version: int = EVIDENCE_PACK_FORMAT_VERSION
    compliance_domain: str | None = None
    description: str | None = None
    record_count: int = 0
    file_count: int = 0
    total_size_bytes: int = 0
    sha256_hash: str = ""


@dataclass
class EvidencePack:
    """An evidence pack containing compliance artifacts.

    Evidence packs are used to submit compliance evidence to auditors.
    They contain a manifest and one or more files with audit records,
    signatures, and other compliance artifacts.
    """

    manifest: EvidenceManifest
    files: dict[str, bytes]
    hmac_key: bytes | None = field(default=None, repr=False)
    """Optional key material used by :meth:`write_zip` when no key is
    passed explicitly.  Excluded from ``repr`` so it does not leak into
    logs."""

    def write_zip(self, output_path: Path, hmac_key: bytes | None = None) -> None:
        """Write the evidence pack as a ZIP file.

        The archive is authenticated with HMAC-SHA256 over the canonical
        ``checksums.json`` bytes using key material resolved from
        *hmac_key*, the pack's stored key, or the documented environment
        variables.  A ``signature.json`` file is written containing the
        pack format version, algorithm, key identifier, checksum-envelope
        digest, and authentication value.

        Raises EvidenceKeyError when no key material is available from the
        *hmac_key* argument, the pack's stored key, or environment
        configuration.
        """
        key = _resolve_evidence_hmac_key(hmac_key or self.hmac_key)
        key_id = _compute_key_id(key)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            manifest_json = json.dumps(
                {
                    "pack_id": self.manifest.pack_id,
                    "created_at": self.manifest.created_at,
                    "created_by": self.manifest.created_by,
                    "pack_format_version": self.manifest.pack_format_version,
                    "compliance_domain": self.manifest.compliance_domain,
                    "description": self.manifest.description,
                    "record_count": self.manifest.record_count,
                    "file_count": self.manifest.file_count,
                    "total_size_bytes": self.manifest.total_size_bytes,
                    "sha256_hash": self.manifest.sha256_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            zf.writestr("manifest.json", manifest_json)

            for filename, content in self.files.items():
                zf.writestr(filename, content)

            manifest_sha = hashlib.sha256(manifest_json.encode()).hexdigest()
            checksums: dict[str, Any] = {
                "manifest_hash": manifest_sha,
                "files": {
                    name: hashlib.sha256(content).hexdigest()
                    for name, content in self.files.items()
                },
            }
            checksums_bytes = _canonical_json_bytes(checksums)
            zf.writestr("checksums.json", checksums_bytes)

            envelope_digest = hashlib.sha256(checksums_bytes).hexdigest()
            auth_value = _hmac.new(key, checksums_bytes, hashlib.sha256).hexdigest()
            signature: dict[str, Any] = {
                "version": EVIDENCE_PACK_FORMAT_VERSION,
                "algorithm": EVIDENCE_PACK_ALGORITHM,
                "key_id": key_id,
                "checksum_envelope_digest": envelope_digest,
                "authentication_value": auth_value,
            }
            zf.writestr("signature.json", _canonical_json_bytes(signature))


def create_evidence_pack(
    created_by: str,
    compliance_domain: str | None = None,
    description: str | None = None,
    audit_records: list[dict[str, Any]] | None = None,
    signatures: list[dict[str, Any]] | None = None,
    manifest_data: dict[str, Any] | None = None,
    hmac_key: bytes | None = None,
) -> EvidencePack:
    """Create an evidence pack with the given contents.

    `hmac_key`, when given, is stored on the pack and used by
    :meth:`write_zip` unless overridden; otherwise ``write_zip`` resolves
    the key from environment configuration.
    """
    files: dict[str, bytes] = {}

    if audit_records:
        audit_json = "\n".join(json.dumps(r, sort_keys=True) for r in audit_records)
        files["audit_records.jsonl"] = audit_json.encode()

    if signatures:
        sig_json = "\n".join(json.dumps(s, sort_keys=True) for s in signatures)
        files["signatures.jsonl"] = sig_json.encode()

    if manifest_data:
        manifest_json = json.dumps(manifest_data, sort_keys=True)
        files["system_manifest.json"] = manifest_json.encode()

    total_size = sum(len(v) for v in files.values())

    pack_manifest = EvidenceManifest(
        created_by=created_by,
        compliance_domain=compliance_domain,
        description=description,
        record_count=len(audit_records) if audit_records else 0,
        file_count=len(files),
        total_size_bytes=total_size,
    )

    return EvidencePack(manifest=pack_manifest, files=files, hmac_key=hmac_key)


def verify_evidence_pack(
    zip_path: Path,
    hmac_key: bytes | None = None,
) -> dict[str, Any]:
    """Verify the externally authenticated integrity of an evidence pack.

    ``valid`` means the pack's canonical ``checksums.json`` bytes are
    authenticated by a valid v2 HMAC-SHA256 signature using the provided
    key material.  Internal checksum consistency is also checked, but
    the HMAC is the authoritative tamper-evidence boundary.

    Unsigned version-1 packs (no ``signature.json``) are reported as
    ``unsigned``, never ``valid``.  Missing key material causes
    verification to fail closed.

    The result and logs never expose key material or the expected
    authentication value.
    """
    if not zip_path.exists():
        return {"valid": False, "error": "File not found"}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())

            if "manifest.json" not in names:
                return {"valid": False, "error": "Missing manifest.json"}

            if "checksums.json" not in names:
                return {"valid": False, "error": "Missing checksums.json"}

            # --- v1 detection: unsigned packs are never valid ---
            if "signature.json" not in names:
                return {
                    "valid": False,
                    "error": "Unsigned evidence pack (format version 1) — not supported",
                    "format_version": 1,
                }

            manifest_data = json.loads(zf.read("manifest.json"))
            checksums_bytes = zf.read("checksums.json")
            checksums_data = json.loads(checksums_bytes)
            signature_data = json.loads(zf.read("signature.json"))

            # --- signature structure validation ---
            sig_version = signature_data.get("version")
            if sig_version != EVIDENCE_PACK_FORMAT_VERSION:
                return {
                    "valid": False,
                    "error": f"Unsupported pack format version: {sig_version}",
                    "format_version": sig_version,
                }

            algorithm = signature_data.get("algorithm")
            if algorithm != EVIDENCE_PACK_ALGORITHM:
                return {"valid": False, "error": f"Unsupported algorithm: {algorithm}"}

            stored_envelope_digest = signature_data.get("checksum_envelope_digest")
            stored_auth_value = signature_data.get("authentication_value")
            stored_key_id = signature_data.get("key_id")

            if not stored_envelope_digest or not stored_auth_value or not stored_key_id:
                return {"valid": False, "error": "Incomplete signature"}

            # --- key resolution (fail closed) ---
            try:
                key = _resolve_evidence_hmac_key(hmac_key)
            except EvidenceKeyError as exc:
                return {"valid": False, "error": str(exc)}

            # --- constant-time authentication checks ---
            # Key identifier first: gives a clear "wrong key" diagnostic
            # without exposing the key or expected auth value.
            expected_key_id = _compute_key_id(key)
            if not _hmac.compare_digest(expected_key_id, stored_key_id):
                return {"valid": False, "error": "Key identifier mismatch"}

            actual_envelope_digest = hashlib.sha256(checksums_bytes).hexdigest()
            if not _hmac.compare_digest(actual_envelope_digest, stored_envelope_digest):
                return {"valid": False, "error": "Checksum envelope digest mismatch"}

            actual_auth_value = _hmac.new(key, checksums_bytes, hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(actual_auth_value, stored_auth_value):
                return {"valid": False, "error": "Authentication value mismatch"}

            # --- internal checksum consistency ---
            actual_manifest_hash = hashlib.sha256(zf.read("manifest.json")).hexdigest()
            if actual_manifest_hash != checksums_data.get("manifest_hash"):
                return {"valid": False, "error": "Manifest hash mismatch"}

            expected_files = set(checksums_data.get("files", {}).keys())
            actual_files = names - _ARCHIVE_META_FILES
            if expected_files != actual_files:
                extra = actual_files - expected_files
                missing = expected_files - actual_files
                if extra:
                    return {"valid": False, "error": f"Unexpected files in pack: {extra}"}
                if missing:
                    return {"valid": False, "error": f"Missing files from pack: {missing}"}

            for filename, expected_hash in checksums_data.get("files", {}).items():
                if filename not in zf.namelist():
                    return {"valid": False, "error": f"Missing file: {filename}"}
                actual_hash = hashlib.sha256(zf.read(filename)).hexdigest()
                if actual_hash != expected_hash:
                    return {"valid": False, "error": f"Hash mismatch for {filename}"}

        return {
            "valid": True,
            "pack_id": manifest_data.get("pack_id"),
            "created_at": manifest_data.get("created_at"),
            "file_count": manifest_data.get("file_count", 0),
            "record_count": manifest_data.get("record_count", 0),
            "format_version": EVIDENCE_PACK_FORMAT_VERSION,
            "key_id": stored_key_id,
        }

    except zipfile.BadZipFile:
        return {"valid": False, "error": "Invalid ZIP file"}
    except json.JSONDecodeError:
        return {"valid": False, "error": "Invalid JSON in pack"}
    except Exception as e:
        return {"valid": False, "error": str(e)}
