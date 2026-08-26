"""Tests for the phlo logs CLI command.

Covers event-level mapping and time-filter parsing, the GraphQL log lookup
against Dagster's current runs/event schema (with an expanded event window
for level filters), the Postgres fallback that honors project env overrides,
and CLI-facing details such as user-facing help text and JSON detection.
"""

from datetime import datetime, timedelta, timezone
import json

from click.testing import CliRunner

from phlo_dagster.cli_logs import (
    _build_logs_query,
    _event_log_row_to_entry,
    _get_log_level,
    _get_logs,
    _get_logs_from_postgres,
    _parse_since,
    logs,
)
from phlo_dagster.cli_logs_display import _is_json


def test_logs_help_is_user_facing() -> None:
    result = CliRunner().invoke(logs, ["--help"])

    assert result.exit_code == 0
    assert "Access and filter Dagster run logs." in result.output
    assert "Args:" not in result.output
    assert "Returns:" not in result.output
    assert "Raises:" not in result.output
    assert "output_json" not in result.output


class TestLogLevelMapping:
    """Test log level mapping from event types."""

    def test_error_event_type(self):
        """Map ERROR event type."""
        assert _get_log_level("STEP_FAILURE") == "ERROR"
        assert _get_log_level("FAILURE") == "ERROR"

    def test_warning_event_type(self):
        """Map WARNING event type."""
        assert _get_log_level("STEP_WARNING") == "WARNING"

    def test_info_event_type(self):
        """Map INFO event type."""
        assert _get_log_level("STEP_SUCCESS") == "INFO"
        assert _get_log_level("STEP_OUTPUT") == "INFO"

    def test_debug_event_type(self):
        """Map DEBUG event type."""
        assert _get_log_level("LOG_MESSAGE") == "DEBUG"
        assert _get_log_level("STEP_INPUT") == "DEBUG"


def test_event_log_row_to_entry_parses_dagster_event_payload() -> None:
    row = {
        "run_id": "run-123",
        "dagster_event_type": "ASSET_MATERIALIZATION",
        "timestamp": datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        "event": (
            '{"level": 20, "user_message": "Materialized value dlt_orders.", '
            '"dagster_event": {"logging_tags": {"job_name": "__ASSET_JOB"}}}'
        ),
    }

    entry = _event_log_row_to_entry(row)

    assert entry == {
        "timestamp": "2026-02-01T10:00:00+00:00",
        "level": "INFO",
        "message": "Materialized value dlt_orders.",
        "event_type": "ASSET_MATERIALIZATION",
        "run_id": "run-123",
        "job_name": "__ASSET_JOB",
        "run_status": "",
    }


def test_event_log_row_to_entry_surfaces_nested_failure_cause() -> None:
    row = {
        "run_id": "run-123",
        "dagster_event_type": "STEP_FAILURE",
        "timestamp": None,
        "event": json.dumps(
            {
                "dagster_event": {
                    "event_specific_data": {
                        "error": {
                            "message": "Exceeded max_retries of 3",
                            "cause": {
                                "message": (
                                    "dlt.extract.exceptions.ResourceNameMissing: "
                                    "Resource name is missing.\nIf you create a resource directly "
                                    "from data pass `name`."
                                ),
                                "cause": None,
                            },
                        }
                    },
                    "logging_tags": {"job_name": "__ASSET_JOB"},
                }
            }
        ),
        "step_key": "dlt_events",
    }

    entry = _event_log_row_to_entry(row)

    assert entry is not None
    assert entry["level"] == "ERROR"
    assert entry["message"] == "Resource name is missing."


class TestTimeParsing:
    """Test time filter parsing."""

    def test_parse_hours(self):
        """Parse hours from time filter."""
        now = datetime.now(timezone.utc)
        result = _parse_since("1h")
        # Should be approximately 1 hour ago
        diff = now - result
        assert timedelta(hours=0.99) < diff < timedelta(hours=1.01)

    def test_parse_minutes(self):
        """Parse minutes from time filter."""
        now = datetime.now(timezone.utc)
        result = _parse_since("30m")
        diff = now - result
        assert timedelta(minutes=29.9) < diff < timedelta(minutes=30.1)

    def test_parse_days(self):
        """Parse days from time filter."""
        now = datetime.now(timezone.utc)
        result = _parse_since("2d")
        diff = now - result
        assert timedelta(days=1.99) < diff < timedelta(days=2.01)

    def test_invalid_time_format(self):
        """Handle invalid time format gracefully."""
        result = _parse_since("invalid")
        # Should default to last 24 hours
        now = datetime.now(timezone.utc)
        diff = now - result
        assert timedelta(hours=23.9) < diff < timedelta(hours=24.1)


class TestJSONDetection:
    """Test JSON content detection."""

    def test_valid_json(self):
        """Detect valid JSON strings."""
        assert _is_json('{"key": "value"}')
        assert _is_json('["item1", "item2"]')
        assert _is_json("123")
        assert _is_json("true")

    def test_invalid_json(self):
        """Detect non-JSON strings."""
        assert not _is_json("plain text")
        assert not _is_json("{invalid json}")
        assert not _is_json("")


class TestLogsCLI:
    """Test logs CLI command."""

    def test_help_message(self):
        """Display help message."""
        runner = CliRunner()
        result = runner.invoke(logs, ["--help"])
        assert result.exit_code == 0
        assert "Access and filter Dagster run logs" in result.output
        assert "--asset" in result.output
        assert "--job" in result.output
        assert "--level" in result.output
        assert "--follow" in result.output

    def test_basic_logs(self):
        """Display basic logs (no services connected = no logs)."""
        runner = CliRunner()
        result = runner.invoke(logs)
        assert result.exit_code == 0

    def test_filter_by_level(self):
        """Filter logs by level."""
        runner = CliRunner()
        result = runner.invoke(logs, ["--level", "ERROR"])
        assert result.exit_code == 0

    def test_json_output(self, monkeypatch):
        """Output logs in JSON format (empty when services disconnected)."""
        from phlo_dagster import cli_logs as logs_module

        runner = CliRunner()
        monkeypatch.setitem(logs.callback.__globals__, "_get_logs", lambda _filters: [])
        monkeypatch.setattr(logs_module, "_get_logs", lambda _filters: [])
        result = runner.invoke(logs, ["--json", "--limit", "2"])
        assert result.exit_code == 0
        assert result.output.strip() == "[]"

    def test_json_follow_is_rejected(self):
        """Machine JSON mode should not silently switch to a live UI."""
        runner = CliRunner()
        result = runner.invoke(logs, ["--json", "--follow"])

        assert result.exit_code == 1
        assert "--json cannot be combined with --follow yet" in result.output

    def test_limit_parameter(self):
        """Limit number of logs."""
        runner = CliRunner()
        result = runner.invoke(logs, ["--limit", "3"])
        assert result.exit_code == 0

    def test_invalid_time_filter(self):
        """Handle invalid time filter gracefully."""
        runner = CliRunner()
        result = runner.invoke(logs, ["--since", "invalid_time"])
        assert result.exit_code == 0


def test_get_logs_uses_project_dagster_port_override(monkeypatch) -> None:
    """GraphQL log lookups should honor project Dagster port overrides."""
    from phlo_dagster import cli_logs as logs_module

    captured: dict[str, str] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"runsOrError": {"runs": []}}}

    monkeypatch.setattr(
        logs_module,
        "_project_env",
        lambda: {"DAGSTER_PORT": "3300", "DAGSTER_WEBSERVER_HOST": "localhost"},
    )

    def fake_post(url: str, **_kwargs) -> FakeResponse:
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(logs_module.http_requests, "post", fake_post)

    assert _get_logs({"limit": 10}) == []
    assert captured["url"] == "http://localhost:3300/graphql"


def test_build_logs_query_matches_current_dagster_runs_schema() -> None:
    """GraphQL query should use Dagster 1.13 run and event field names."""
    query = _build_logs_query({"limit": 7})

    assert "runsOrError(limit: 7)" in query
    assert "results {" in query
    assert "eventConnection(limit: 7)" in query
    assert "runs(limit:" not in query
    assert "... on StepFailureEvent" not in query
    assert "... on StepSuccessEvent" not in query
    assert "ExecutionStepFailureEvent" in query
    assert "ExecutionStepSuccessEvent" in query


def test_build_logs_query_expands_event_window_for_level_filters() -> None:
    query = _build_logs_query({"limit": 20, "level": "ERROR"})

    assert "runsOrError(limit: 20)" in query
    assert "eventConnection(limit: 400)" in query


def test_get_logs_parses_current_dagster_graphql_shape(monkeypatch) -> None:
    """GraphQL parser should read runsOrError.results[].eventConnection.events."""
    from phlo_dagster import cli_logs as logs_module

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": {
                    "runsOrError": {
                        "results": [
                            {
                                "runId": "run-1",
                                "jobName": "__ASSET_JOB",
                                "status": "SUCCESS",
                                "eventConnection": {
                                    "events": [
                                        {
                                            "__typename": "ExecutionStepSuccessEvent",
                                            "eventType": "STEP_SUCCESS",
                                            "message": "step ok",
                                            "timestamp": "2026-05-04T09:00:00Z",
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }

    monkeypatch.setattr(logs_module, "_project_env", lambda: {"DAGSTER_PORT": "13000"})
    monkeypatch.setattr(logs_module.http_requests, "post", lambda *_args, **_kwargs: FakeResponse())

    assert _get_logs({"limit": 1}) == [
        {
            "timestamp": "2026-05-04T09:00:00Z",
            "level": "INFO",
            "message": "step ok",
            "event_type": "STEP_SUCCESS",
            "run_id": "run-1",
            "job_name": "__ASSET_JOB",
            "run_status": "SUCCESS",
        }
    ]


def test_get_logs_prefers_postgres_for_level_filters(monkeypatch) -> None:
    from phlo_dagster import cli_logs as logs_module

    monkeypatch.setattr(
        logs_module,
        "_get_logs_from_postgres",
        lambda _filters: [
            {
                "timestamp": "",
                "level": "ERROR",
                "message": "root cause",
                "event_type": "STEP_FAILURE",
                "run_id": "run-1",
                "job_name": "__ASSET_JOB",
                "run_status": "",
            }
        ],
    )
    monkeypatch.setattr(
        logs_module.http_requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("skip GraphQL")),
    )

    result = _get_logs({"limit": 20, "level": "ERROR"})

    assert result[0]["message"] == "root cause"


def test_get_logs_from_postgres_uses_project_env(monkeypatch) -> None:
    """Postgres fallback should honor project DB env overrides."""
    from phlo_dagster import cli_logs as logs_module

    captured: dict[str, object] = {}

    class FakeCursor:
        def execute(self, *_args, **_kwargs) -> None:
            return None

        def fetchall(self) -> list:
            return []

        def close(self) -> None:
            return None

    class FakeConnection:
        def cursor(self, *_args, **_kwargs) -> FakeCursor:
            return FakeCursor()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        logs_module,
        "_project_env",
        lambda: {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "15432",
            "POSTGRES_DB": "custom",
            "POSTGRES_USER": "user",
            "POSTGRES_PASSWORD": "secret",
        },
    )

    def fake_connect(**kwargs) -> FakeConnection:
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr(logs_module.psycopg2, "connect", fake_connect)

    assert _get_logs_from_postgres({}) == []
    assert captured == {
        "host": "localhost",
        "port": 15432,
        "database": "custom",
        "user": "user",
        "password": "secret",
    }


def test_get_logs_from_postgres_expands_level_filter_window(monkeypatch) -> None:
    """ERROR lookups should not miss failures just outside the latest display limit."""
    from phlo_dagster import cli_logs as logs_module

    captured: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, _query, params) -> None:
            captured["params"] = params

        def fetchall(self) -> list[dict]:
            return [
                {
                    "run_id": "run-1",
                    "dagster_event_type": "STEP_SUCCESS",
                    "timestamp": None,
                    "event": "{}",
                    "step_key": "dlt_events",
                },
                {
                    "run_id": "run-1",
                    "dagster_event_type": "STEP_FAILURE",
                    "timestamp": None,
                    "event": '{"level": 40, "user_message": "failed"}',
                    "step_key": "dlt_events",
                },
            ]

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args) -> None:
            return None

        def cursor(self, *_args, **_kwargs) -> FakeCursor:
            return FakeCursor()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        logs_module,
        "_project_env",
        lambda: {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "15432",
            "POSTGRES_DB": "phlo",
            "POSTGRES_USER": "phlo",
            "POSTGRES_PASSWORD": "phlo",
        },
    )
    monkeypatch.setattr(logs_module.psycopg2, "connect", lambda **_kwargs: FakeConnection())

    result = _get_logs_from_postgres({"limit": 1, "level": "ERROR"})

    assert captured["params"][-1] == 200
    assert len(result) == 1
    assert result[0]["level"] == "ERROR"


def test_log_retrieval_handles_bounded_and_large_limits():
    """Log retrieval succeeds for bounded and large limits without timing
    assertions: wall-clock bounds are environment-dependent and flake under
    instrumentation (coverage runs measured 2x)."""
    runner = CliRunner()
    for limit in ("100", "1000"):
        result = runner.invoke(logs, ["--limit", limit])
        assert result.exit_code == 0
