"""Tests for required durable audit delivery.

Required events must reach at least one successful durable replica sink;
logging-only and memory-backed sinks do not qualify, sink failure propagates
(fail closed for allowed mutations), and development logging stays
best-effort.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phlo.audit.events import (
    AuditEventEmitter,
    AuditPersistenceError,
    CanonicalAuditEvent,
    LoggingAuditSink,
)
from phlo.capabilities.interfaces import AuthPrincipal, ResourceRef
from phlo.compliance.audit import InMemoryAuditStore, TamperEvidentAuditSink
from phlo.security.enforcement import EnforcementContext, enforce


class DurableSink:
    is_durable = True

    def __init__(self) -> None:
        self.events: list[CanonicalAuditEvent] = []

    def write(self, event: CanonicalAuditEvent) -> None:
        self.events.append(event)


class FailingDurableSink:
    is_durable = True

    def write(self, event: CanonicalAuditEvent) -> None:
        raise RuntimeError("database unavailable")


def _event() -> CanonicalAuditEvent:
    return CanonicalAuditEvent(event_type="authorization", surface="phlo-api")


def test_required_delivery_appends_to_durable_sink() -> None:
    emitter = AuditEventEmitter("phlo-api")
    sink = DurableSink()
    emitter.add_sink(sink)
    event = _event()

    emitter.emit(event, require_durable=True)

    assert sink.events == [event]


def test_required_delivery_rejects_logging_only_configuration() -> None:
    emitter = AuditEventEmitter("phlo-api")
    emitter.add_sink(LoggingAuditSink())

    with pytest.raises(AuditPersistenceError, match="no durable audit sink"):
        emitter.emit(_event(), require_durable=True)


def test_required_delivery_propagates_durable_sink_failure() -> None:
    emitter = AuditEventEmitter("phlo-api")
    emitter.add_sink(FailingDurableSink())

    with pytest.raises(AuditPersistenceError, match="durable audit persistence failed"):
        emitter.emit(_event(), require_durable=True)


def test_required_delivery_accepts_one_successful_durable_replica() -> None:
    emitter = AuditEventEmitter("phlo-api")
    sink = DurableSink()
    emitter.add_sink(FailingDurableSink())
    emitter.add_sink(sink)
    event = _event()

    emitter.emit(event, require_durable=True)

    assert sink.events == [event]


def test_development_logging_remains_best_effort() -> None:
    emitter = AuditEventEmitter("phlo-api")
    emitter.add_sink(FailingDurableSink())

    emitter.emit(_event())


def test_memory_backed_audit_sink_cannot_satisfy_durable_delivery() -> None:
    emitter = AuditEventEmitter("phlo-api")
    emitter.add_sink(TamperEvidentAuditSink(InMemoryAuditStore()))

    with pytest.raises(AuditPersistenceError, match="no durable audit sink"):
        emitter.emit(_event(), require_durable=True)


def test_required_audit_failure_fails_closed_for_allowed_mutation(monkeypatch) -> None:
    monkeypatch.setattr(EnforcementContext, "_instance", None)
    ctx = EnforcementContext.get_instance()
    backend = MagicMock()
    backend.explain_decision.return_value = MagicMock(
        allowed=True, reason_code=None, policy_id=None, explanation=None
    )
    ctx._authorization_backend = backend
    emitter = AuditEventEmitter("core")
    emitter.add_sink(FailingDurableSink())
    ctx._audit_emitter = emitter
    ctx._initialized = True

    result = enforce(
        principal=AuthPrincipal(subject="operator", principal_type="user", groups=()),
        action="dataset.delete",
        resource=ResourceRef(resource_type="dataset", resource_id="orders"),
        surface="phlo-api",
        require_durable_audit=True,
    )

    assert result.variant == "error"
    assert result.reason_code == "audit_persistence_failed"
    EnforcementContext.reset_instance()
