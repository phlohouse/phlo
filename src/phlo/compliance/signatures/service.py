"""Electronic signature service.

Provides the core signature service for critical action signing in regulated deployments.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from phlo.audit.events import AuditEventEmitter, AuditEventType, CanonicalAuditEvent
from phlo.capabilities.interfaces import AuthenticatedSession
from phlo.compliance.signatures.step_up import SessionConfirmChallenge, StepUpAuthChallenge
from phlo.compliance.signatures.types import SignatureRecord, SignatureRequest
from phlo.logging import get_logger

logger = get_logger(__name__)


class SignatureRepository(Protocol):
    """Durable signature persistence and atomic single-use verification."""

    def save_signature(self, record: SignatureRecord) -> None:
        """Persist an issued signature."""

    def save_signature_idempotent(
        self,
        record: SignatureRecord,
        *,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> SignatureRecord:
        """Persist or return the original signature for an idempotent request."""

    def consume_signature(self, signature_id: str, expected: SignatureRequest) -> bool:
        """Match and atomically consume the signature for the expected action."""


DEFAULT_CRITICAL_ACTIONS = frozenset(
    [
        "dataset.publish",
        "asset.approve",
        "admin.manage",
        "settings.manage",
        "catalog.manage",
    ]
)


@dataclass
class SignatureServiceConfig:
    """Configuration for the signature service."""

    critical_actions: frozenset[str] = DEFAULT_CRITICAL_ACTIONS
    """Actions that require explicit electronic signatures."""

    step_up_challenge: StepUpAuthChallenge | None = None
    """Step-up challenge implementation. Uses SessionConfirmChallenge if None."""


class SignatureService:
    """Service for managing electronic signatures.

    Validates signature requests, performs step-up authentication,
    records signatures as audit events, and checks if actions require signatures.
    """

    def __init__(
        self,
        config: SignatureServiceConfig | None = None,
        audit_emitter: AuditEventEmitter | None = None,
        signature_repository: SignatureRepository | None = None,
    ) -> None:
        """Initialize the signature service.

        Falls back to default configuration when config is omitted; the
        step-up challenge comes from config or defaults to session confirm.
        """
        self._config = config or SignatureServiceConfig()
        self._audit_emitter = audit_emitter
        self._signature_repository = signature_repository
        self._step_up = self._config.step_up_challenge or SessionConfirmChallenge()

    def require_signature(self, action: str, resource_type: str) -> bool:
        """Return True when the canonical action requires a signature."""
        return action in self._config.critical_actions

    def sign(
        self,
        request: SignatureRequest,
        session: AuthenticatedSession,
        *,
        idempotency_key: str | None = None,
    ) -> SignatureRecord:
        """Create an electronic signature for a record.

        Validates the signer against the session principal (ValueError on
        mismatch), performs step-up authentication (PermissionError on
        failure), and records the signature as an audit event.
        """
        if request.signer_subject != session.principal.subject:
            raise ValueError(
                f"Signer subject mismatch: request={request.signer_subject}, session={session.principal.subject}"
            )
        if (
            not request.action
            or not request.record_type
            or not request.record_id
            or not request.record_version
        ):
            raise ValueError("Signature action, target, and target version are required")

        step_up_result = self._step_up.challenge(session)
        if not step_up_result.success:
            raise PermissionError(f"Step-up authentication failed: {step_up_result.message}")

        record = SignatureRecord.from_request(
            request,
            authentication_assurance=step_up_result.assurance_level,
        )
        if self._signature_repository is None:
            raise RuntimeError("Durable signature storage is not configured")
        if idempotency_key is None:
            self._signature_repository.save_signature(record)
        else:
            request_digest = hashlib.sha256(
                json.dumps(
                    {
                        "signer_subject": request.signer_subject,
                        "meaning": request.meaning,
                        "record_type": request.record_type,
                        "record_id": request.record_id,
                        "record_version": request.record_version,
                        "action": request.action,
                        "justification": request.justification,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            record = self._signature_repository.save_signature_idempotent(
                record,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )

        self._emit_signature_event(record, session)

        logger.info(
            "signature_recorded",
            signature_id=record.signature_id,
            signer=record.signer_subject,
            meaning=record.meaning,
            record_type=record.record_type,
            record_id=record.record_id,
        )

        return record

    def _emit_signature_event(
        self,
        record: SignatureRecord,
        session: AuthenticatedSession,
    ) -> None:
        """Emit a signature audit event."""
        if self._audit_emitter is None:
            return

        event = CanonicalAuditEvent(
            event_type=AuditEventType.SIGNATURE,
            surface="compliance",
            actor_subject=record.signer_subject,
            actor_type=session.principal.principal_type,
            actor_roles=session.principal.groups,
            authentication_source=session.auth_method or "unknown",
            action="signature.create",
            resource_type=record.record_type,
            resource_id=record.record_id,
            decision="allow",
            reason_code="signature_recorded",
            outcome="success",
            attributes={
                "signature_id": record.signature_id,
                "meaning": record.meaning,
                "action": record.action,
                "record_version": record.record_version,
                "authentication_assurance": record.authentication_assurance,
                "signature_hash": record.signature_hash,
                "justification": record.justification,
            },
        )
        self._audit_emitter.emit(event)

    def verify_signature(self, signature_id: str, expected: SignatureRequest) -> bool:
        """Atomically consume a valid signature bound to the exact action/target."""
        if self._signature_repository is None:
            return False
        return self._signature_repository.consume_signature(signature_id, expected)
