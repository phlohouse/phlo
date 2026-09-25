"""Tests for phlo.logging helpers.

Covers record-to-event routing, correlation context binding, log path
rendering, and logging setup behaviour.
"""

from __future__ import annotations

import logging
import sys
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from phlo.logging import (
    LoggingSettings,
    LogRouterHandler,
    _record_to_event,
    _render_log_file_path,
    bind_context,
    clear_context,
    get_bound_correlation_context,
    get_logger,
    log_event,
    setup_logging,
    suppress_log_routing,
)
from tests.helpers import RecordingBus

pytestmark = pytest.mark.core_regression


def _make_record(
    *,
    msg: Any,
    level: int = logging.INFO,
    name: str = "phlo.tests.logging",
    lineno: int = 7,
) -> logging.LogRecord:
    """Create a deterministic `LogRecord` for routing tests."""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=lineno,
        msg=msg,
        args=(),
        exc_info=None,
        func="test_func",
    )


def test_render_log_file_path_resolves_template(tmp_path: Path) -> None:
    """Resolves ``{YMD}`` placeholders into a concrete log path."""
    template = str(tmp_path / "{YMD}.log")
    path = _render_log_file_path(template)

    assert path is not None
    assert path.parent == tmp_path
    assert path.suffix == ".log"
    assert len(path.stem) == 8


def test_render_log_file_path_respects_project_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolves relative templates under ``PHLO_PROJECT_PATH`` when set."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    template = ".phlo/logs/{YMD}.log"

    path = _render_log_file_path(template)

    assert path is not None
    assert path.parent == tmp_path / ".phlo" / "logs"
    assert path.suffix == ".log"


def test_render_log_file_path_warns_on_unknown_placeholder(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Logs a warning and returns ``None`` for unknown placeholders."""
    template = str(tmp_path / "{NOPE}.log")

    with caplog.at_level(logging.WARNING, logger="phlo.logging"):
        path = _render_log_file_path(template)

    assert path is None
    assert "log_file_template_placeholder_unknown" in caplog.text


def test_setup_logging_writes_to_file(tmp_path: Path) -> None:
    """Configures file logging and verifies emitted content is persisted."""
    template = str(tmp_path / "phlo-{YMD}.log")
    settings = LoggingSettings(
        level="INFO",
        log_format="json",
        router_enabled=False,
        service_name="phlo-tests",
        log_file_template=template,
        environment="test",
    )

    setup_logging(settings, force=True)
    logger = get_logger("phlo.tests.logging")
    logger.info("hello file logging", test_case="setup_logging")

    for handler in logging.root.handlers:
        handler.flush()

    path = _render_log_file_path(template)
    assert path is not None
    assert path.exists()
    contents = path.read_text()
    assert "hello file logging" in contents
    assert '"environment": "test"' in contents


def test_setup_logging_redacts_sensitive_fields(tmp_path: Path) -> None:
    """Redacts sensitive values before rendering structured logs."""
    template = str(tmp_path / "redacted-{YMD}.log")
    settings = LoggingSettings(
        level="INFO",
        log_format="json",
        router_enabled=False,
        service_name="phlo-tests",
        log_file_template=template,
        environment="test",
    )

    setup_logging(settings, force=True)
    logger = get_logger("phlo.tests.logging")
    logger.info("sensitive test", api_token="abc123", nested={"password": "p@ss"})

    for handler in logging.root.handlers:
        handler.flush()

    path = _render_log_file_path(template)
    assert path is not None
    assert path.exists()
    contents = path.read_text()
    assert "<redacted>" in contents
    assert "abc123" not in contents
    assert "p@ss" not in contents


def test_auto_format_keeps_stderr_quiet_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keeps CLI-facing stderr free of internal structured diagnostics."""
    stream = StringIO()
    monkeypatch.setattr(sys, "stderr", stream)

    settings = LoggingSettings(
        level="INFO",
        log_format="auto",
        router_enabled=False,
        service_name="phlo-cli",
        log_file_template=None,
        environment="test",
    )

    setup_logging(settings, force=True)
    logger = get_logger("phlo.tests.logging", service="phlo-cli")
    logger.info("project_initialized", project_name="demo", file_count=7)

    for handler in logging.root.handlers:
        handler.flush()

    rendered = stream.getvalue()
    assert rendered == ""


def test_console_format_renders_human_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserves opt-in compact terminal diagnostics for debugging."""
    stream = StringIO()
    monkeypatch.setattr(sys, "stderr", stream)

    settings = LoggingSettings(
        level="INFO",
        log_format="console",
        router_enabled=False,
        service_name="phlo-cli",
        log_file_template=None,
        environment="test",
    )

    setup_logging(settings, force=True)
    logger = get_logger("phlo.tests.logging", service="phlo-cli")
    logger.info("project_initialized", project_name="demo", file_count=7)

    for handler in logging.root.handlers:
        handler.flush()

    rendered = stream.getvalue()
    assert rendered
    assert not rendered.lstrip().startswith("{")
    assert "project_initialized" in rendered
    assert "project_name=demo" in rendered
    assert "file_count=7" in rendered
    assert "timestamp=" not in rendered


def test_json_format_still_renders_structured_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserves explicit JSON stream logging for machines."""
    stream = StringIO()
    monkeypatch.setattr(sys, "stderr", stream)

    settings = LoggingSettings(
        level="INFO",
        log_format="json",
        router_enabled=False,
        service_name="phlo-worker",
        log_file_template=None,
        environment="test",
    )

    setup_logging(settings, force=True)
    logger = get_logger("phlo.tests.logging", service="phlo-worker")
    logger.info("worker_started", worker_id="w1")

    for handler in logging.root.handlers:
        handler.flush()

    rendered = stream.getvalue()
    assert rendered.lstrip().startswith("{")
    assert '"event": "worker_started"' in rendered
    assert '"worker_id": "w1"' in rendered


def test_record_to_event_extracts_tags_and_metadata() -> None:
    """Builds event fields from record extras and strips consumed keys."""
    record = _make_record(
        msg={
            "event": "asset materialized",
            "service": "ingestion-worker",
            "run_id": 42,
            "asset_key": "raw.orders",
            "trace_id": "abc123",
            "span_id": "def456",
            "tags": {"team": "analytics", "attempt": 2},
            "custom_field": "ok",
            "api_token": "secret-value",
        }
    )

    event = _record_to_event(record, "phlo-default")

    assert event is not None
    assert event.service == "ingestion-worker"
    assert event.message == "asset materialized"
    assert event.run_id == "42"
    assert event.asset_key == "raw.orders"
    assert event.tags == {
        "team": "analytics",
        "attempt": "2",
        "service": "ingestion-worker",
    }
    assert event.metadata["custom_field"] == "ok"
    assert event.metadata["trace_id"] == "abc123"
    assert event.metadata["span_id"] == "def456"
    assert event.metadata["api_token"] == "<redacted>"
    assert "service" not in event.metadata
    assert "run_id" not in event.metadata
    assert "asset_key" not in event.metadata
    assert "tags" not in event.metadata


def test_record_to_event_redacts_urls_in_fields_message_and_exception() -> None:
    url = "https://u:p@host/x?token=abc&ok=1"
    try:
        raise RuntimeError(f"request failed for {url}")
    except RuntimeError:
        exception = sys.exc_info()
        record = _make_record(msg=f"request failed for {url}")
        record.url = url
        record.presigned_url = "https://host/x?X-Amz-Signature=sig&ok=1"
        record.exc_info = exception

    event = _record_to_event(record, "phlo-default")

    assert event is not None
    expected = "https://host/x?token=REDACTED&ok=1"
    assert event.message == f"request failed for {expected}"
    assert event.metadata["url"] == expected
    assert event.metadata["presigned_url"] == "https://host/x?X-Amz-Signature=REDACTED&ok=1"
    assert "password" not in event.metadata["exception"]
    assert "abc" not in event.metadata["exception"]
    assert expected in event.metadata["exception"]


def test_record_to_event_preserves_benign_urls_and_query_params() -> None:
    record = _make_record(
        msg="https://host/path?region=west&version=2 Not a URL?token=visible "
        "https:///broken?token=visible"
    )

    event = _record_to_event(record, "phlo-default")

    assert event is not None
    assert event.message == (
        "https://host/path?region=west&version=2 Not a URL?token=visible "
        "https:///broken?token=visible"
    )


def test_get_bound_correlation_context_reads_structlog_contextvars() -> None:
    bind_context(run_id="run-99", asset_key="silver.orders", trace_id="abc123")

    try:
        correlation = get_bound_correlation_context()
    finally:
        clear_context()

    assert correlation.run_id == "run-99"
    assert correlation.asset_key == "silver.orders"
    assert correlation.trace_id == "abc123"


def test_bound_correlation_merges_observe_context_without_overriding_structlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phlo.telemetry.logging_correlation",
        lambda: {"run_id": "observe-run", "job_id": "observe-job", "asset_key": "raw.users"},
    )
    bind_context(run_id="logging-run")

    try:
        correlation = get_bound_correlation_context()
    finally:
        clear_context()

    assert correlation.run_id == "logging-run"
    assert correlation.job_name == "observe-job"
    assert correlation.asset_key == "raw.users"


def test_record_to_event_merges_bound_correlation_context() -> None:
    bind_context(run_id="run-77", asset_key="bronze.orders", trace_id="abc123")

    try:
        record = _make_record(msg="context-backed event")
        event = _record_to_event(record, "phlo-default")
    finally:
        clear_context()

    assert event is not None
    assert event.run_id == "run-77"
    assert event.asset_key == "bronze.orders"
    assert event.correlation.trace_id == "abc123"
    assert event.metadata["trace_id"] == "abc123"


def test_log_router_handler_emit_routes_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routes converted events to hooks and the canonical observe runtime."""

    class FailableRecordingBus(RecordingBus):
        def __init__(self) -> None:
            super().__init__()
            self.should_fail = False

        def emit(self, event: Any) -> None:
            if self.should_fail:
                raise RuntimeError("emit failed")
            self.events.append(event)

    bus = FailableRecordingBus()
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr("phlo.hooks.bus.get_hook_bus", lambda: bus)
    monkeypatch.setattr(
        "phlo.telemetry.emit",
        lambda name, **fields: observed.append({"name": name, **fields}),
    )
    handler = LogRouterHandler(service_name="router-service")

    routed = _make_record(
        msg={
            "event": "routed https://u:p@host/x?token=secret&ok=1",
            "run_id": "run-1",
            "asset_key": "raw.users",
            "tags": {"source": "test"},
            "rows": 12,
            "url": "https://u:p@host/x?token=secret&ok=1",
        }
    )
    handler.emit(routed)

    assert len(bus.events) == 1
    assert bus.events[0].message == "routed https://host/x?token=REDACTED&ok=1"
    assert bus.events[0].metadata["url"] == "https://host/x?token=REDACTED&ok=1"
    assert bus.events[0].tags["source"] == "test"
    assert observed[0]["name"] == "application.log"
    assert observed[0]["attributes"]["message"] == bus.events[0].message
    assert observed[0]["attributes"]["url"] == bus.events[0].metadata["url"]
    assert observed[0]["attributes"]["rows"] == 12
    assert observed[0]["correlation"] == {
        "run_id": "run-1",
        "asset_key": "raw.users",
    }
    assert observed[0]["tags"] == {"source": "test", "service": "router-service"}

    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", lambda failed: errors.append(failed))
    bus.should_fail = True
    failing = _make_record(msg="will fail")

    handler.emit(failing)

    assert errors == [failing]


def test_setup_logging_places_router_before_existing_handlers() -> None:
    """Preserves structured records when framework handlers mutate messages."""
    existing = logging.StreamHandler()
    logging.root.addHandler(existing)
    settings = LoggingSettings(level="INFO", log_format="auto", router_enabled=True)

    try:
        setup_logging(settings, force=True)

        assert isinstance(logging.root.handlers[0], LogRouterHandler)
        assert logging.root.handlers.index(existing) > 0
    finally:
        logging.root.removeHandler(existing)


def test_suppress_log_routing_blocks_emit_then_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevents routing while active and restores routing afterward."""
    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.bus.get_hook_bus", lambda: bus)
    handler = LogRouterHandler(service_name="router-service")
    record = _make_record(msg="suppressed check")

    with suppress_log_routing():
        handler.emit(record)

    assert bus.events == []

    handler.emit(record)

    assert len(bus.events) == 1
    assert bus.events[0].message == "suppressed check"


def test_log_event_falls_back_when_logger_rejects_structured_kwargs() -> None:
    """Falls back to plain message formatting on `TypeError`."""

    class LegacyLogger:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def info(self, message: str) -> None:
            self.messages.append(message)

    logger = LegacyLogger()

    log_event(logger, "info", "legacy event", run_id="run-1", attempt=3)

    assert logger.messages == ["legacy event run_id=run-1 attempt=3"]


def test_bound_correlation_uses_only_a_valid_active_otel_span() -> None:
    """Hook correlation adopts optional OTel context without inventing IDs."""
    trace = pytest.importorskip("opentelemetry.trace")
    span_context = trace.SpanContext(
        trace_id=int("a" * 32, 16),
        span_id=int("b" * 16, 16),
        is_remote=False,
        trace_flags=trace.TraceFlags(1),
        trace_state=trace.TraceState(),
    )

    with trace.use_span(trace.NonRecordingSpan(span_context), end_on_exit=False):
        correlation = get_bound_correlation_context()

    assert correlation.trace_id == "a" * 32
    assert correlation.span_id == "b" * 16
    assert correlation.trace_flags == "01"
