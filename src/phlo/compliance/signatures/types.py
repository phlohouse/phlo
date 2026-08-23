"""Electronic signature types.

Defines the types for electronic signatures in regulated deployments.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
from dataclasses import dataclass, field
from dataclasses import replace as _dataclass_replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

PHLO_SIGNATURE_HMAC_KEY_ENV = "PHLO_SIGNATURE_HMAC_KEY"
PHLO_AUDIT_HMAC_KEY_ENV = "PHLO_AUDIT_HMAC_KEY"


def _get_signature_hmac_key() -> bytes:
    """Return the HMAC key for signatures, or a dev default."""
    key = os.environ.get(PHLO_SIGNATURE_HMAC_KEY_ENV) or os.environ.get(PHLO_AUDIT_HMAC_KEY_ENV)
    if key:
        return key.encode()
    from phlo.security.mode import is_regulated

    if is_regulated():
        raise RuntimeError(
            f"{PHLO_SIGNATURE_HMAC_KEY_ENV} or {PHLO_AUDIT_HMAC_KEY_ENV} is required in regulated mode"
        )
    return b"phlo-dev-signature-key"


class SignatureMeaning(StrEnum):
    """Meaning or purpose of a signature."""

    APPROVED = "approved"
    """The signer has approved the record."""

    RELEASED = "released"
    """The signer has released the record for use."""

    REVIEWED = "reviewed"
    """The signer has reviewed the record."""

    ACKNOWLEDGED = "acknowledged"
    """The signer has acknowledged the record."""

    AUTHORED = "authored"
    """The signer authored the record."""


@dataclass(frozen=True, kw_only=True)
class SignatureRequest:
    """A request to sign a record.

    Represents an intent to create an electronic signature for a specific
    record with a specific meaning.
    """

    signer_subject: str
    """Subject identifier of the signer."""

    meaning: SignatureMeaning
    """The meaning of the signature."""

    record_type: str
    """Type of the record being signed (e.g., "dataset", "config", "policy")."""

    record_id: str
    """Unique identifier of the record being signed."""

    record_version: str
    """Version hash or state hash of the record being signed."""

    justification: str | None = None
    """Optional justification for the signature."""


@dataclass(frozen=True, kw_only=True)
class SignatureRecord:
    """A completed electronic signature record.

    Represents a completed electronic signature with all metadata required
    for compliance auditing.
    """

    signature_id: str = field(default_factory=lambda: str(uuid4()))
    """Unique identifier for this signature."""

    signer_subject: str
    """Subject identifier of the signer."""

    meaning: SignatureMeaning
    """The meaning of the signature."""

    record_type: str
    """Type of the record that was signed."""

    record_id: str
    """Unique identifier of the record that was signed."""

    record_version: str
    """Version hash or state hash of the record at signing time."""

    justification: str | None = None
    """Justification provided at signing time."""

    signed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    """ISO 8601 timestamp when the signature was created."""

    authentication_assurance: str = "session"
    """Assurance level of authentication (e.g., "session", "mfa", "re-authenticated")."""

    signature_hash: str = ""
    """SHA-256 hash of the canonical representation of this signature."""

    @classmethod
    def from_request(
        cls,
        request: SignatureRequest,
        authentication_assurance: str = "session",
    ) -> SignatureRecord:
        """Create a SignatureRecord from a SignatureRequest.

        The signature_hash is an HMAC-SHA256 keyed with a secret so that
        forging a valid signature requires the secret, not just the payload.
        authentication_assurance records the level of authentication used.
        """
        record = cls(
            signer_subject=request.signer_subject,
            meaning=request.meaning,
            record_type=request.record_type,
            record_id=request.record_id,
            record_version=request.record_version,
            justification=request.justification,
            authentication_assurance=authentication_assurance,
        )

        canonical = json.dumps(
            {
                "signature_id": record.signature_id,
                "signer_subject": record.signer_subject,
                "meaning": record.meaning,
                "record_type": record.record_type,
                "record_id": record.record_id,
                "record_version": record.record_version,
                "signed_at": record.signed_at,
                "authentication_assurance": record.authentication_assurance,
            },
            sort_keys=True,
        )
        hmac_key = _get_signature_hmac_key()
        signature_hash = _hmac.new(hmac_key, canonical.encode(), hashlib.sha256).hexdigest()

        return _dataclass_replace(record, signature_hash=signature_hash)
