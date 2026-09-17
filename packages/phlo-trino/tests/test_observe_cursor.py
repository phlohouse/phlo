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
    inner.query_id = "20240101_000000_00042_x"
    inner.rowcount = 17
    cursor = _ObservedCursor(inner, catalog="lake", schema="silver")

    cursor.execute("select * from t where secret = %s", ("p@ss",))

    assert scope_kwargs == [
        {"sql": "select * from t where secret = %s", "catalog": "lake", "schema": "silver"}
    ]
    assert len(bound) == 1
    assert isinstance(bound[0], _RecordingScope)
    assert ("set", {"query_id": "20240101_000000_00042_x", "row_count": 17}) in sink
    exits = [v for kind, v in sink if kind == "exit"]
    assert exits == [None]
    inner.execute.assert_called_once_with("select * from t where secret = %s", ("p@ss",))


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
