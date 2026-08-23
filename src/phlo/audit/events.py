"""Canonical audit event library.

This module provides the shared canonical audit event schema and infrastructure
for regulated mode. All approved surfaces must emit audit events using this
contract for authorization decisions, authentication events, and privileged
mutations.

Audit Event Contract v1:
    All audit events must include the top-level fields defined in CanonicalAuditEvent.
    The envelope is consistent across authn, authz, and mutation events.

Regulated Mode Requirements:
    - One shared library owns this contract
    - Authn and authz events use the same envelope
    - Explanatory decision data comes from AuthorizationDecision
    - Privileged mutation allows must emit allow events
    - Denied actions must emit deny events
    - Settings/history records must also emit canonical audit events
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from phlo.logging import get_logger

logger = get_logger(__name__)

# Schema version for the canonical audit event contract
AUDIT_SCHEMA_VERSION = "1.0"


class AuditPersistenceError(RuntimeError):
    """Raised when an operation requires durable audit persistence."""


class AuditEventType(StrEnum):
    """Types of audit events."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    MUTATION = "mutation"
    ADMIN = "admin"
    SYSTEM = "system"
    SIGNATURE = "signature"


class AuditDecision(StrEnum):
    """Decision outcomes for audit events."""

    ALLOW = "allow"
    DENY = "deny"
    ERROR = "error"
    SKIP = "skip"


class AuditOutcome(StrEnum):
    """Execution outcomes for audit events."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass(frozen=True)
class CanonicalAuditEvent:
    """Canonical audit event v1.

    This is the normative contract for audit events in regulated mode.
    All approved surfaces must emit events using this schema.

    Required Fields:
        schema_version: Audit schema version (always "1.0").
        event_type: Type of audit event (authentication, authorization, etc.).
        event_id: Unique identifier for this event (UUID).
        timestamp: ISO 8601 timestamp of the event.
        surface: Surface that generated the event (e.g., "phlo-api", "dagster").

    Actor Fields:
        actor_subject: Subject identifier of the actor.
        actor_type: Type of actor (user, service, platform).
        actor_roles: Roles assigned to the actor at decision time.
        authentication_source: Source of authentication (IdP, proxy, etc.).

    Action Fields:
        action: Canonical action being attempted.
        resource_type: Canonical resource type being accessed.
        resource_id: Stable resource identifier.

    Decision Fields:
        decision: Allow, deny, error, or skip.
        reason_code: Machine-readable reason for the decision.
        policy_id: ID of the policy that produced the decision (if applicable).

    Context Fields:
        request_id: Request or correlation ID.
        run_id: Run ID for pipeline executions.
        source_ip: Source IP address when available.

    Mutation Fields (when applicable):
        target_state_before: State before mutation.
        target_state_after: State after mutation.
        change_reason: Reason for the change.

    Outcome:
        outcome: Success, failure, or partial.
    """

    # Required fields
    schema_version: str = AUDIT_SCHEMA_VERSION
    event_type: str = ""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    surface: str = ""

    # Actor fields
    actor_subject: str = ""
    actor_type: str = ""
    actor_roles: tuple[str, ...] = ()
    authentication_source: str = ""

    # Action fields
    action: str = ""
    resource_type: str = ""
    resource_id: str = ""

    # Decision fields
    decision: str = ""
    reason_code: str = ""
    policy_id: str | None = None
    explanation: str | None = None

    # Context fields
    request_id: str | None = None
    correlation_id: str = ""
    parent_correlation_id: str = ""
    run_id: str | None = None
    source_ip: str | None = None

    # Mutation fields
    target_state_before: dict[str, Any] | None = None
    target_state_after: dict[str, Any] | None = None
    change_reason: str | None = None

    # Outcome
    outcome: str = ""

    # Additional attributes for extensibility
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize every field, converting actor_roles to a JSON-safe list."""
        result = asdict(self)
        # Convert tuples to lists for JSON serialization
        result["actor_roles"] = list(self.actor_roles)
        return result

    @classmethod
    def from_authorization_decision(
        cls,
        *,
        surface: str,
        actor_subject: str,
        actor_type: str,
        actor_roles: tuple[str, ...],
        authentication_source: str,
        action: str,
        resource_type: str,
        resource_id: str,
        decision: str,
        reason_code: str,
        policy_id: str | None = None,
        explanation: str | None = None,
        request_id: str | None = None,
        correlation_id: str = "",
        parent_correlation_id: str = "",
        source_ip: str | None = None,
        outcome: str = "",
    ) -> CanonicalAuditEvent:
        """Build an AUTHORIZATION event from a decision's actor, action, and outcome fields."""
        return cls(
            event_type=AuditEventType.AUTHORIZATION,
            surface=surface,
            actor_subject=actor_subject,
            actor_type=actor_type,
            actor_roles=actor_roles,
            authentication_source=authentication_source,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            reason_code=reason_code,
            policy_id=policy_id,
            explanation=explanation,
            request_id=request_id,
            correlation_id=correlation_id,
            parent_correlation_id=parent_correlation_id,
            source_ip=source_ip,
            outcome=outcome,
        )


class AuditEventEmitter:
    """Emitter for canonical audit events.

    This class provides the interface for emitting audit events in regulated mode.
    All approved surfaces use this emitter to ensure consistent audit trails.

    The emitter supports multiple sinks and can be configured for different
    retention and routing policies.

    Example:
        >>> from phlo.audit.events import AuditEventEmitter, CanonicalAuditEvent
        >>> emitter = AuditEventEmitter()
        >>> event = CanonicalAuditEvent(...)
        >>> emitter.emit(event)
    """

    def __init__(self, surface: str) -> None:
        """Record this surface's name, stamped onto emitted events that lack one."""
        self.surface = surface
        self._sinks: list[AuditEventSink] = []

    def add_sink(self, sink: AuditEventSink) -> None:
        """Register a sink to receive subsequently emitted events."""
        self._sinks.append(sink)

    def emit(self, event: CanonicalAuditEvent, *, require_durable: bool = False) -> None:
        """Deliver the event to every configured sink, stamping the surface when unset.

        Sink failures are logged and swallowed, but when `require_durable` is
        set and no durable sink persists the event, AuditPersistenceError is
        raised.
        """
        # Create a new event with the correct surface if not set
        if not event.surface:
            from dataclasses import replace

            event = replace(event, surface=self.surface)

        logger.debug(
            "audit_event_emitted",
            event_id=event.event_id,
            event_type=event.event_type,
            surface=event.surface,
            actor=event.actor_subject,
            action=event.action,
            decision=event.decision,
        )

        # Write to every sink even after an earlier sink fails, so one broken
        # sink cannot drop events from healthy ones. Non-durable sinks are
        # best-effort: their failures are logged and swallowed. A failure only
        # escalates when durability was required and no durable sink persisted
        # the event.
        durable_sink_seen = False
        durable_sink_succeeded = False
        durable_error: Exception | None = None
        for sink in self._sinks:
            is_durable = getattr(sink, "is_durable", False)
            durable_sink_seen = durable_sink_seen or is_durable
            try:
                sink.write(event)
                durable_sink_succeeded = durable_sink_succeeded or is_durable
            except Exception as e:
                logger.error(
                    "audit_sink_write_failed",
                    sink_type=type(sink).__name__,
                    event_id=event.event_id,
                    error=str(e),
                )
                if require_durable and is_durable:
                    durable_error = e

        if require_durable and not durable_sink_seen:
            raise AuditPersistenceError("no durable audit sink is configured")
        if durable_error is not None and not durable_sink_succeeded:
            raise AuditPersistenceError("durable audit persistence failed") from durable_error

    def emit_authorization(
        self,
        *,
        surface: str | None = None,
        actor_subject: str,
        actor_type: str,
        actor_roles: tuple[str, ...],
        authentication_source: str,
        action: str,
        resource_type: str,
        resource_id: str,
        decision: str,
        reason_code: str,
        policy_id: str | None = None,
        explanation: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        parent_correlation_id: str = "",
        source_ip: str | None = None,
        outcome: str = "",
        require_durable: bool = False,
    ) -> None:
        """Build an authorization event from decision details and emit it.

        Takes the same actor/action/decision fields as
        CanonicalAuditEvent.from_authorization_decision; pass
        `require_durable` to enforce durable persistence.
        """
        event = CanonicalAuditEvent.from_authorization_decision(
            surface=surface or self.surface,
            actor_subject=actor_subject,
            actor_type=actor_type,
            actor_roles=actor_roles,
            authentication_source=authentication_source,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            reason_code=reason_code,
            policy_id=policy_id,
            explanation=explanation,
            request_id=request_id,
            correlation_id=correlation_id or "",
            parent_correlation_id=parent_correlation_id,
            source_ip=source_ip,
            outcome=outcome,
        )
        self.emit(event, require_durable=require_durable)

    def emit_mutation(
        self,
        *,
        actor_subject: str,
        actor_type: str,
        actor_roles: tuple[str, ...],
        authentication_source: str,
        action: str,
        resource_type: str,
        resource_id: str,
        target_state_before: dict[str, Any] | None = None,
        target_state_after: dict[str, Any] | None = None,
        change_reason: str | None = None,
        request_id: str | None = None,
        source_ip: str | None = None,
        outcome: str = "",
    ) -> None:
        """Emit a mutation audit event recording before/after state and change reason."""
        event = CanonicalAuditEvent(
            event_type=AuditEventType.MUTATION,
            surface=self.surface,
            actor_subject=actor_subject,
            actor_type=actor_type,
            actor_roles=actor_roles,
            authentication_source=authentication_source,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=AuditDecision.ALLOW,  # Mutations are only audited when allowed
            reason_code="mutation_executed",
            target_state_before=target_state_before,
            target_state_after=target_state_after,
            change_reason=change_reason,
            request_id=request_id,
            source_ip=source_ip,
            outcome=outcome,
        )
        self.emit(event)


class AuditEventSink:
    """Base class for audit event sinks.

    Implementations must provide the write() method.
    """

    is_durable = False
    # Sinks that guarantee durable persistence set this True; the emitter
    # consults it when deciding whether a require_durable emit succeeded.

    def write(self, event: CanonicalAuditEvent) -> None:
        """Persist the event; the base implementation raises NotImplementedError."""
        raise NotImplementedError


class LoggingAuditSink(AuditEventSink):
    """Audit sink that writes to structured logs.

    This is the default sink for audit events. Events are written
    as structured log entries at INFO level.
    """

    def __init__(self, logger_name: str = "phlo.audit") -> None:
        """Create the sink backed by the named logger."""
        self._logger = get_logger(logger_name)

    def write(self, event: CanonicalAuditEvent) -> None:
        """Log the event as a structured INFO entry under canonical_audit_event."""
        self._logger.info(
            "canonical_audit_event",
            **event.to_dict(),
        )


def create_default_emitter(surface: str) -> AuditEventEmitter:
    """Return an emitter preconfigured with the structured logging sink."""
    emitter = AuditEventEmitter(surface)
    emitter.add_sink(LoggingAuditSink())
    return emitter
