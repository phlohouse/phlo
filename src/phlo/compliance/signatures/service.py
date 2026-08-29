"""Electronic signature service.

Provides the core signature service for critical action signing in regulated deployments.
"""

from __future__ import annotations

from dataclasses import dataclass

from phlo.audit.events import AuditEventEmitter, AuditEventType, CanonicalAuditEvent
from phlo.capabilities.interfaces import AuthenticatedSession
from phlo.compliance.signatures.step_up import SessionConfirmChallenge, StepUpAuthChallenge
from phlo.compliance.signatures.types import SignatureRecord, SignatureRequest
from phlo.logging import get_logger

logger = get_logger(__name__)

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
    ) -> None:
        """Initialize the signature service.

        Falls back to default configuration when config is omitted; the
        step-up challenge comes from config or defaults to session confirm.
        """
        self._config = config or SignatureServiceConfig()
        self._audit_emitter = audit_emitter
        self._step_up = self._config.step_up_challenge or SessionConfirmChallenge()

    def require_signature(self, action: str, resource_type: str) -> bool:
        """Return True when the canonical action requires a signature."""
        return action in self._config.critical_actions

    def sign(
        self,
        request: SignatureRequest,
        session: AuthenticatedSession,
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

        step_up_result = self._step_up.challenge(session)
        if not step_up_result.success:
            raise PermissionError(f"Step-up authentication failed: {step_up_result.message}")

        record = SignatureRecord.from_request(
            request,
            authentication_assurance=step_up_result.assurance_level,
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
                "record_version": record.record_version,
                "authentication_assurance": record.authentication_assurance,
                "signature_hash": record.signature_hash,
                "justification": record.justification,
            },
        )
        self._audit_emitter.emit(event)

    def verify_signature(
        self,
        record_id: str,
        record_version: str,
    ) -> bool:
        """Deny verification until a signature record can be consulted.

        Signature records are not persisted or consulted yet, so this service
        cannot confirm that a record was signed or that its version matches.
        """
        return False
