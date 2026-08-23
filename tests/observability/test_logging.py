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


def test_get_bound_correlation_context_reads_structlog_contextvars() -> None:
    bind_context(run_id="run-99", asset_key="silver.orders", trace_id="abc123")

    try:
        correlation = get_bound_correlation_context()
    finally:
        clear_context()

    assert correlation.run_id == "run-99"
    assert correlation.asset_key == "silver.orders"
    assert correlation.trace_id == "abc123"


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
    """Routes converted events and reports failures through `handleError`."""

    class FailableRecordingBus(RecordingBus):
        def __init__(self) -> None:
            super().__init__()
            self.should_fail = False

        def emit(self, event: Any) -> None:
            if self.should_fail:
                raise RuntimeError("emit failed")
            self.events.append(event)

    bus = FailableRecordingBus()
    monkeypatch.setattr("phlo.hooks.bus.get_hook_bus", lambda: bus)
    handler = LogRouterHandler(service_name="router-service")

    routed = _make_record(msg={"event": "routed", "tags": {"source": "test"}})
    handler.emit(routed)

    assert len(bus.events) == 1
    assert bus.events[0].message == "routed"
    assert bus.events[0].tags["source"] == "test"

    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", lambda failed: errors.append(failed))
    bus.should_fail = True
    failing = _make_record(msg="will fail")

    handler.emit(failing)

    assert errors == [failing]


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
