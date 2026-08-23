"""Tests for Trino publishing helpers.

Covers table-reference candidate ordering with fallback from
catalog-qualified to schema-qualified names, retry rules keyed on
table-not-found introspection errors only, publish-target default
resolution, and correlation propagation onto publish and lineage events.
"""

from __future__ import annotations

import pytest

from phlo.hooks.events import LineageEvent, PublishEvent, TelemetryEvent
import phlo_trino.publishing as publishing
from phlo_trino.publishing import (
    _describe_trino_table,
    _is_retryable_introspection_error,
    _trino_table_ref_candidates,
    _resolve_publish_target,
)


class _FakeCursor:
    """Test cursor double that replays canned Trino responses."""

    def __init__(self, trino: "_FakeTrino") -> None:
        """Initialize the fake cursor with its parent client."""
        self._trino = trino
        self._rows: list[tuple[object, ...]] = []

    def __enter__(self) -> "_FakeCursor":
        """Return this cursor instance for context-manager usage."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        """Close the context manager; arguments are ignored."""
        return None

    def execute(self, query: str) -> None:
        """Record and resolve a fake query, raising when the response is an exception."""
        self._trino.queries.append(query)
        response = self._trino.get_response(query)
        if isinstance(response, Exception):
            raise response
        self._rows = list(response or [])

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return rows from the last executed query."""
        return self._rows


class _FakeTrino:
    """Test Trino client double with deterministic query responses."""

    def __init__(
        self,
        responses: dict[str, object],
        sequence_responses: dict[str, list[object]] | None = None,
    ) -> None:
        """Initialize static and queued query responses."""
        self.responses = responses
        self.sequence_responses = {
            query: list(values) for query, values in (sequence_responses or {}).items()
        }
        self.queries: list[str] = []

    def get_response(self, query: str) -> object:
        """Return the next queued response for a query, else its static response."""
        queued = self.sequence_responses.get(query)
        if queued:
            return queued.pop(0)
        return self.responses.get(query)

    def cursor(self) -> _FakeCursor:
        """Create a fake cursor bound to this client."""
        return _FakeCursor(self)


def test_trino_table_ref_candidates_include_three_and_two_part_variants() -> None:
    """Ensures table reference candidates include three-part and schema/table forms."""
    refs = _trino_table_ref_candidates("iceberg.raw_marts.posts_mart")

    assert refs == [
        '"iceberg"."raw_marts"."posts_mart"',
        "iceberg.raw_marts.posts_mart",
        '"raw_marts"."posts_mart"',
        "raw_marts.posts_mart",
    ]


def test_describe_trino_table_falls_back_to_schema_table_reference() -> None:
    """Verifies fallback to schema/table reference after catalog-qualified failures."""
    trino = _FakeTrino(
        {
            'DESCRIBE "iceberg"."raw_marts"."posts_mart"': RuntimeError("http 404"),
            'SHOW COLUMNS FROM "iceberg"."raw_marts"."posts_mart"': RuntimeError("http 404"),
            "DESCRIBE iceberg.raw_marts.posts_mart": RuntimeError("http 404"),
            "SHOW COLUMNS FROM iceberg.raw_marts.posts_mart": RuntimeError("http 404"),
            'DESCRIBE "raw_marts"."posts_mart"': [
                ("id", "bigint"),
                ("title", "varchar"),
            ],
        }
    )

    columns, resolved_ref = _describe_trino_table(trino, "iceberg.raw_marts.posts_mart")

    assert resolved_ref == '"raw_marts"."posts_mart"'
    assert columns == [
        ("id", "bigint", '"id"'),
        ("title", "text", '"title"'),
    ]
    assert trino.queries[:3] == [
        'DESCRIBE "iceberg"."raw_marts"."posts_mart"',
        "DESCRIBE iceberg.raw_marts.posts_mart",
        'DESCRIBE "raw_marts"."posts_mart"',
    ]
    assert 'SHOW COLUMNS FROM "iceberg"."raw_marts"."posts_mart"' not in trino.queries
    assert "SHOW COLUMNS FROM iceberg.raw_marts.posts_mart" not in trino.queries


def test_describe_trino_table_retries_after_table_not_found() -> None:
    """Verifies retry occurs when introspection fails with table-not-found error."""
    trino = _FakeTrino(
        responses={},
        sequence_responses={
            'DESCRIBE "iceberg"."raw_marts"."posts_mart"': [
                RuntimeError(
                    "TrinoUserError(type=USER_ERROR, name=TABLE_NOT_FOUND, "
                    'message="table does not exist")'
                ),
                [("id", "bigint"), ("title", "varchar")],
            ]
        },
    )

    columns, resolved_ref = _describe_trino_table(trino, "iceberg.raw_marts.posts_mart")

    assert resolved_ref == '"iceberg"."raw_marts"."posts_mart"'
    assert columns == [
        ("id", "bigint", '"id"'),
        ("title", "text", '"title"'),
    ]
    assert trino.queries.count('DESCRIBE "iceberg"."raw_marts"."posts_mart"') == 2


def test_describe_trino_table_skips_non_retryable_candidate_after_first_failure() -> None:
    """Verifies non-retryable candidates are not retried after first failure."""
    trino = _FakeTrino(
        responses={},
        sequence_responses={
            'DESCRIBE "iceberg"."raw_marts"."posts_mart"': [
                RuntimeError("permission denied"),
                RuntimeError("should not execute"),
            ],
            "DESCRIBE iceberg.raw_marts.posts_mart": [
                RuntimeError(
                    "TrinoUserError(type=USER_ERROR, name=TABLE_NOT_FOUND, "
                    'message="table does not exist")'
                ),
                [("id", "bigint"), ("title", "varchar")],
            ],
        },
    )

    columns, resolved_ref = _describe_trino_table(trino, "iceberg.raw_marts.posts_mart")

    assert resolved_ref == "iceberg.raw_marts.posts_mart"
    assert columns == [
        ("id", "bigint", '"id"'),
        ("title", "text", '"title"'),
    ]
    assert trino.queries.count('DESCRIBE "iceberg"."raw_marts"."posts_mart"') == 1


def test_retryable_introspection_error_uses_structured_fields() -> None:
    """Verifies structured error fields are recognized as retryable."""

    class _StructuredTrinoError(RuntimeError):
        """Structured exception stub mimicking Trino client errors."""

        def __init__(self) -> None:
            """Initialize the structured Trino error stub."""
            super().__init__("opaque server error")
            self.error_name = "TABLE_NOT_FOUND"
            self.error_type = "USER_ERROR"

    assert _is_retryable_introspection_error(_StructuredTrinoError())


def test_resolve_publish_target_uses_wrapper_defaults() -> None:
    class _PublishTarget:
        resource = object()
        target_system = "postgres"
        default_schema = "serving"

    resource, target_system, schema = _resolve_publish_target(_PublishTarget(), target_schema=None)

    assert resource is _PublishTarget.resource
    assert target_system == "postgres"
    assert schema == "serving"


def test_publish_marts_emits_correlation(monkeypatch) -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.events: list[object] = []

        def emit(self, event: object) -> None:
            self.events.append(event)

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    inserted_rows: list[tuple[object, ...]] = []

    def _record_execute_values(_cursor, _query, rows, page_size) -> None:
        assert page_size == 1000
        inserted_rows.extend(rows)

    monkeypatch.setattr(publishing, "execute_values", _record_execute_values)

    class _FakePostgresCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, query) -> None:
            return None

    class _FakeTrinoCursor:
        def __init__(self) -> None:
            self._rows: list[tuple[object, ...]] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, query: str) -> None:
            if query.startswith("DESCRIBE ") or query.startswith("SHOW COLUMNS FROM "):
                self._rows = [
                    ("order_id", "bigint"),
                    ("status", "varchar"),
                    ("amount", "double"),
                ]
                return
            if query.startswith("SELECT "):
                self._rows = [
                    (1, "placed", 10.0),
                    (2, "paid", 12.5),
                    (3, "fulfilled", 18.0),
                ]
                return
            raise AssertionError(f"Unexpected Trino query: {query}")

        def fetchall(self) -> list[tuple[object, ...]]:
            return list(self._rows)

        def fetchmany(self, batch_size: int) -> list[tuple[object, ...]]:
            batch = self._rows[:batch_size]
            self._rows = self._rows[batch_size:]
            return batch

    class _FakeTrino:
        def cursor(self) -> _FakeTrinoCursor:
            return _FakeTrinoCursor()

    class _FakePostgres:
        def cursor(self) -> _FakePostgresCursor:
            return _FakePostgresCursor()

        def commit(self) -> None:
            return None

    class _UnpartitionedDagsterContext:
        asset_key = "publish_orders"
        run_id = "run-88"
        job_name = "__ASSET_JOB"
        has_partition_key = False

        @property
        def partition_key(self) -> str:
            raise RuntimeError("Cannot access partition_key for a non-partitioned run")

    stats = publishing._publish_marts(
        context=_UnpartitionedDagsterContext(),
        trino=_FakeTrino(),
        postgres=_FakePostgres(),
        target_system="postgres",
        tables_to_publish={"orders": "silver.orders"},
        data_source="orders",
        target_schema="marts",
        batch_size=1000,
    )

    assert stats["orders"].row_count == 3
    assert stats["orders"].column_count == 3
    assert inserted_rows == [
        (1, "placed", 10.0),
        (2, "paid", 12.5),
        (3, "fulfilled", 18.0),
    ]
    publish_event = next(event for event in bus.events if isinstance(event, PublishEvent))
    telemetry_event = next(event for event in bus.events if isinstance(event, TelemetryEvent))
    lineage_event = next(event for event in bus.events if isinstance(event, LineageEvent))

    assert publish_event.correlation.run_id == "run-88"
    assert publish_event.correlation.asset_key == "publish_orders"
    assert publish_event.correlation.partition_key is None
    assert telemetry_event.correlation.run_id == "run-88"
    assert telemetry_event.correlation.asset_key == "publish_orders"
    assert telemetry_event.correlation.partition_key is None
    assert lineage_event.correlation.run_id == "run-88"
    assert lineage_event.correlation.asset_key == "publish_orders"
    assert lineage_event.correlation.partition_key is None


def test_resolve_source_table_reference_uses_logical_ref(monkeypatch) -> None:
    class _Relation:
        def render(self) -> str:
            return '"iceberg"."marts"."orders"'

    calls: list[str] = []
    monkeypatch.setattr(
        publishing,
        "ref",
        lambda model_name: calls.append(model_name) or _Relation(),
    )

    assert publishing._resolve_source_table_reference("ref:mrt_orders") == (
        '"iceberg"."marts"."orders"'
    )
    assert calls == ["mrt_orders"]


def test_resolve_source_table_reference_preserves_physical_table() -> None:
    assert publishing._resolve_source_table_reference("marts.orders") == "marts.orders"


@pytest.mark.parametrize("source_table", ["ref:", "ref:   "])
def test_resolve_source_table_reference_rejects_empty_model_name(source_table: str) -> None:
    with pytest.raises(ValueError, match="must include a dbt model name"):
        publishing._resolve_source_table_reference(source_table)


def test_resolve_partition_key_does_not_swallow_runtime_specific_errors() -> None:
    class _ContextWithProviderError:
        @property
        def partition_key(self) -> str:
            raise RuntimeError("Cannot access partition_key for a non-partitioned run")

    with pytest.raises(RuntimeError, match="Cannot access partition_key"):
        publishing._resolve_partition_key(_ContextWithProviderError())
