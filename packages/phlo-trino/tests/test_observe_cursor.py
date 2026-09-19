"""Tests for the ``trino.query`` instrumentation on the resource cursor proxy.

``_ObservedCursor`` wraps every ``execute`` in a ``trino.query`` observe scope,
binds the ambient run entity so provider events derive ``run://dagster/<id>``
rather than a phantom ``run://trino/<id>``, and attaches the Trino query id
and row count. SQL text is never recorded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

import phlo.telemetry as phlo_observe
from phlo_trino.resource import _ObservedCursor


class _RecordingScope:
    def __init__(self, sink: list[tuple[str, Any]]) -> None:
        self.sink = sink

    def __enter__(self) -> _RecordingScope:
        self.sink.append(("enter", None))
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.sink.append(("exit", exc_type))
        return None

    def set(self, **attrs: Any) -> None:
        self.sink.append(("set", attrs))


def test_execute_emits_trino_query_with_run_entity(monkeypatch) -> None:
    sink: list[tuple[str, Any]] = []
    scope_kwargs: list[dict[str, Any]] = []
    bound: list[Any] = []

    def _trino_query(**kwargs: Any) -> _RecordingScope:
        scope_kwargs.append(kwargs)
        return _RecordingScope(sink)

    monkeypatch.setattr(phlo_observe, "trino_query", _trino_query)
    monkeypatch.setattr(phlo_observe, "bind_run_entity", bound.append)

    inner = MagicMock()
    inner.description = [("value",)]
    inner.query_id = "20240101_000000_00042_x"
    inner.rowcount = 17
    cursor = _ObservedCursor(inner, catalog="lake", schema="silver")

    cursor.execute("select * from t where secret = %s", ("p@ss",))

    assert scope_kwargs == [
        {"sql": "select * from t where secret = %s", "catalog": "lake", "schema": "silver"}
    ]
    assert len(bound) == 1
    assert isinstance(bound[0], _RecordingScope)
    inner.execute.assert_called_once_with("select * from t where secret = %s", ("p@ss",))
    # execute() returning is not completion — the scope stays open while
    # the result set is consumed so fetch errors and duration are observed.
    assert [v for kind, v in sink if kind == "exit"] == []

    cursor.fetchall()

    assert ("set", {"query_id": "20240101_000000_00042_x", "row_count": 17}) in sink
    assert [v for kind, v in sink if kind == "exit"] == [None]


def test_execute_returns_proxy_so_chained_calls_stay_observed(monkeypatch) -> None:
    """``execute()`` must return the proxy, not the inner cursor: DB-API
    callers chain ``.execute(...)``, and handing back the inner cursor would
    bypass the observe scope on every subsequent call."""
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope([]))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    cursor = _ObservedCursor(inner, catalog=None, schema=None)
    chained = cursor.execute("select 1")
    assert chained is cursor
    chained.execute("select 2")
    assert inner.execute.call_count == 2


def test_execute_failure_records_exception(monkeypatch) -> None:
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.execute.side_effect = RuntimeError("trino exploded")
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    with pytest.raises(RuntimeError, match="trino exploded"):
        cursor.execute("select 1")
    exits = [v for kind, v in sink if kind == "exit"]
    assert exits == [RuntimeError]


def test_passthrough_attributes_delegate(monkeypatch) -> None:
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope([]))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    cursor = _ObservedCursor(inner, catalog="c", schema="s")
    assert cursor.description is inner.description
    cursor.close()
    inner.close.assert_called_once()


def test_cursor_iteration_delegates(monkeypatch) -> None:
    """The real Trino cursor is iterable; the proxy must keep `for row in
    cursor` working — implicit dunder lookup never consults __getattr__."""
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope([]))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.__next__.side_effect = [(1,), (2,), (3,), StopIteration]
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    assert list(cursor) == [(1,), (2,), (3,)]

    inner.__next__.side_effect = None
    inner.__next__.return_value = ("a",)
    assert next(cursor) == ("a",)


def test_fetchall_ends_the_query_scope(monkeypatch) -> None:
    """The scope ends when the result set is consumed, not when execute
    returns — fetch-time errors and full duration belong to the event."""
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = [("value",)]
    inner.fetchall.return_value = [(1,), (2,)]
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("select * from t")
    assert [v for kind, v in sink if kind == "exit"] == []

    assert cursor.fetchall() == [(1,), (2,)]
    assert [v for kind, v in sink if kind == "exit"] == [None]


def test_fetch_error_fails_the_query_event(monkeypatch) -> None:
    """Errors raised while fetching escape ``execute`` but not the scope."""
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = [("value",)]
    inner.fetchall.side_effect = RuntimeError("result page exploded")
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("select * from t")
    with pytest.raises(RuntimeError, match="result page exploded"):
        cursor.fetchall()
    assert [v for kind, v in sink if kind == "exit"] == [RuntimeError]


def test_iteration_exhaustion_ends_the_query_scope(monkeypatch) -> None:
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = [("value",)]
    inner.__next__.side_effect = [(1,), StopIteration]
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("select * from t")
    assert list(cursor) == [(1,)]
    assert [v for kind, v in sink if kind == "exit"] == [None]


def test_iteration_error_fails_the_query_event(monkeypatch) -> None:
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = [("value",)]
    inner.__next__.side_effect = RuntimeError("stream exploded")
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("select * from t")
    with pytest.raises(RuntimeError, match="stream exploded"):
        list(cursor)
    assert [v for kind, v in sink if kind == "exit"] == [RuntimeError]


def test_close_ends_an_unconsumed_query_scope(monkeypatch) -> None:
    """Abandoning a result set still emits the event when the cursor closes."""
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = [("value",)]
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("select * from t")
    cursor.close()
    assert [v for kind, v in sink if kind == "exit"] == [None]
    inner.close.assert_called_once()


def test_statement_without_result_set_closes_at_execute(monkeypatch) -> None:
    """DDL/DML with ``description is None`` has nothing to consume."""
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = None
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("alter table t execute optimize")
    assert [v for kind, v in sink if kind == "exit"] == [None]


def test_re_execute_closes_the_previous_query_scope(monkeypatch) -> None:
    """A new execute abandons the previous result set: its scope closes."""
    sink: list[tuple[str, Any]] = []
    monkeypatch.setattr(phlo_observe, "trino_query", lambda **kw: _RecordingScope(sink))
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda _e: None)

    inner = MagicMock()
    inner.description = [("value",)]
    cursor = _ObservedCursor(inner, catalog=None, schema=None)

    cursor.execute("select 1")
    cursor.execute("select 2")
    exits = [v for kind, v in sink if kind == "exit"]
    assert exits == [None]  # first scope closed; second still open
