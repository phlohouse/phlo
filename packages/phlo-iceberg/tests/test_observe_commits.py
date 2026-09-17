"""Tests for the ``iceberg.commit`` instrumentation helpers in tables.py.

``_iceberg_commit_scope`` opens the observe scope with the table, ref, and
operation; ``_finish_iceberg_commit`` records the post-commit snapshot id on
both the attributes and the correlation (snapshot_id is a correlation key the
observer folds into the run). Both must degrade quietly when the table's
snapshot introspection fails.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import phlo.telemetry as phlo_observe
from phlo_iceberg.tables import (
    _current_snapshot_id,
    _finish_iceberg_commit,
    _iceberg_commit_scope,
)


class _RecordingScope:
    def __init__(self) -> None:
        self.sets: list[dict[str, Any]] = []
        self.correlations: list[dict[str, Any]] = []
        self.entities: dict[str, Any] = {}

    def __enter__(self) -> _RecordingScope:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def set(self, **attrs: Any) -> None:
        self.sets.append(attrs)

    def set_correlation(self, **values: Any) -> None:
        self.correlations.append(values)

    def set_entity(self, role: str, identifier: Any) -> None:
        self.entities[role] = identifier


def _patched_scope(monkeypatch) -> tuple[list[dict[str, Any]], list[_RecordingScope]]:
    """Stub ``phlo_observe.iceberg_commit`` with recording scope factories."""
    calls: list[dict[str, Any]] = []
    scopes: list[_RecordingScope] = []

    def _factory(**kw: Any) -> _RecordingScope:
        calls.append(kw)
        scope = _RecordingScope()
        scopes.append(scope)
        return scope

    monkeypatch.setattr(phlo_observe, "iceberg_commit", _factory)
    return calls, scopes


def test_commit_scope_forwards_table_ref_and_operation(monkeypatch) -> None:
    calls, scopes = _patched_scope(monkeypatch)

    with _iceberg_commit_scope(
        "silver.samples", "main", "append", snapshot_id_before="10", rows_added=5
    ) as scope:
        pass
    assert calls == [
        {
            "table": "silver.samples",
            "branch": "main",
            "operation": "append",
            "snapshot_id_before": "10",
            "rows_added": 5,
        }
    ]
    assert scope is scopes[0]


def test_commit_scope_restamps_branch_with_resolved_catalog_system(monkeypatch) -> None:
    """The SDK pins branch entities to ``branch://nessie/<ref>``; under a
    snapshot-promotion catalog the ref is owned by that provider, so the scope
    must restamp the entity against the resolved catalog system."""
    calls, scopes = _patched_scope(monkeypatch)
    monkeypatch.setattr(
        "phlo_iceberg.tables.resolve_capability",
        lambda _type: SimpleNamespace(name="polaris"),
    )
    monkeypatch.setattr(
        phlo_observe,
        "branch_entity_id",
        lambda branch, *, system="nessie": f"branch://{system}/{branch}",
    )
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda scope: None)

    with _iceberg_commit_scope("silver.samples", "pipeline-run-1", "append"):
        pass

    assert scopes[0].entities["branch"] == "branch://polaris/pipeline-run-1"


def test_commit_scope_defaults_branch_system_to_nessie(monkeypatch) -> None:
    """No configured catalog capability keeps the historical Nessie naming."""
    calls, scopes = _patched_scope(monkeypatch)
    monkeypatch.setattr("phlo_iceberg.tables.resolve_capability", lambda _type: None)
    monkeypatch.setattr(
        phlo_observe,
        "branch_entity_id",
        lambda branch, *, system="nessie": f"branch://{system}/{branch}",
    )
    monkeypatch.setattr(phlo_observe, "bind_run_entity", lambda scope: None)

    with _iceberg_commit_scope("silver.samples", "main", "append"):
        pass

    assert scopes[0].entities["branch"] == "branch://nessie/main"


def test_finish_commit_records_snapshot_after() -> None:
    scope = _RecordingScope()
    table = MagicMock()
    table.current_snapshot.return_value = MagicMock(snapshot_id=42)

    _finish_iceberg_commit(scope, table)

    assert scope.sets == [{"snapshot_id_after": "42"}]
    assert scope.correlations == [{"snapshot_id": "42"}]


def test_finish_commit_skips_when_no_snapshot() -> None:
    scope = _RecordingScope()
    table = MagicMock()
    table.current_snapshot.return_value = None

    _finish_iceberg_commit(scope, table)

    assert scope.sets == []
    assert scope.correlations == []


def test_finish_commit_survives_snapshot_failure() -> None:
    scope = _RecordingScope()
    table = MagicMock()
    table.current_snapshot.side_effect = RuntimeError("catalog gone")

    _finish_iceberg_commit(scope, table)
    assert scope.sets == []


def test_finish_commit_survives_builder_failure() -> None:
    scope = MagicMock()
    scope.set.side_effect = RuntimeError("builder exploded")
    table = MagicMock()
    table.current_snapshot.return_value = MagicMock(snapshot_id=7)

    _finish_iceberg_commit(scope, table)


def test_current_snapshot_id_stringifies() -> None:
    table = MagicMock()
    table.current_snapshot.return_value = MagicMock(snapshot_id=123)
    assert _current_snapshot_id(table) == "123"

    table.current_snapshot.return_value = None
    assert _current_snapshot_id(table) is None

    table.current_snapshot.side_effect = RuntimeError("nope")
    assert _current_snapshot_id(table) is None
