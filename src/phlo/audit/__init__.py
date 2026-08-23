"""Canonical audit event library.

This module provides the shared canonical audit event schema and infrastructure
for regulated mode. All approved surfaces must emit audit events using this
contract for authorization decisions, authentication events, and privileged
mutations.

Exports:
    AUDIT_SCHEMA_VERSION: Version of the canonical audit event schema.
    CanonicalAuditEvent: The canonical audit event dataclass.
    AuditEventType: Types of audit events.
    AuditDecision: Decision outcomes.
    AuditOutcome: Execution outcomes.
    AuditPersistenceError: Raised when durable audit persistence fails.
    AuditEventEmitter: Emitter for audit events.
    AuditEventSink: Base class for audit sinks.
    LoggingAuditSink: Default logging-based sink.
    create_default_emitter: Factory for default emitter.
"""

from __future__ import annotations

from phlo.audit.events import (
    AUDIT_SCHEMA_VERSION,
    AuditDecision,
    AuditEventEmitter,
    AuditEventSink,
    AuditEventType,
    AuditOutcome,
    AuditPersistenceError,
    CanonicalAuditEvent,
    LoggingAuditSink,
    create_default_emitter,
)

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditDecision",
    "AuditEventEmitter",
    "AuditEventSink",
    "AuditEventType",
    "AuditOutcome",
    "AuditPersistenceError",
    "CanonicalAuditEvent",
    "LoggingAuditSink",
    "create_default_emitter",
]
