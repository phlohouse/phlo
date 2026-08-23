"""Verify apply_partition_scope SQL rewriting: WHERE injection, AND
composition with existing filters, and rolling-window date bounds."""

from __future__ import annotations

import importlib
from datetime import date

from phlo_pandera.partitioning import PartitionScope, apply_partition_scope


def test_apply_partition_scope_adds_where_for_partition_key() -> None:
    """Verify partition key scope appends a WHERE predicate."""

    scope = PartitionScope(
        partition_key="2024-01-02",
        partition_column="_phlo_partition_date",
        rolling_window_days=7,
        full_table=False,
    )
    assert (
        apply_partition_scope("SELECT * FROM some_table", scope=scope)
        == "SELECT * FROM some_table\nWHERE _phlo_partition_date = DATE '2024-01-02'"
    )


def test_apply_partition_scope_appends_and_when_where_exists() -> None:
    """Verify partition key scope appends AND to existing WHERE clauses."""

    scope = PartitionScope(
        partition_key="2024-01-02",
        partition_column="_phlo_partition_date",
        rolling_window_days=7,
        full_table=False,
    )
    assert (
        apply_partition_scope("SELECT * FROM some_table\nWHERE x = 1", scope=scope)
        == "SELECT * FROM some_table\nWHERE x = 1\nAND _phlo_partition_date = DATE '2024-01-02'"
    )


def test_apply_partition_scope_rolling_window(monkeypatch) -> None:
    """Verify rolling window scope uses today's date minus window days."""

    class _Date(date):
        """Deterministic date replacement for rolling window tests."""

        @classmethod
        def today(cls):  # type: ignore[override]
            """Return a fixed date for reproducible assertions."""

            return cls(2024, 1, 10)

    module = importlib.import_module("phlo_pandera.partitioning")
    monkeypatch.setattr(module, "date", _Date)

    scope = PartitionScope(
        partition_key=None,
        partition_column="_phlo_partition_date",
        rolling_window_days=7,
        full_table=False,
    )
    assert (
        apply_partition_scope("SELECT * FROM some_table", scope=scope)
        == "SELECT * FROM some_table\nWHERE _phlo_partition_date >= DATE '2024-01-03'"
    )


def test_apply_partition_scope_full_table_noop() -> None:
    """Verify full-table scope leaves SQL unchanged."""

    scope = PartitionScope(
        partition_key="2024-01-02",
        partition_column="_phlo_partition_date",
        rolling_window_days=7,
        full_table=True,
    )
    assert (
        apply_partition_scope("SELECT * FROM some_table", scope=scope) == "SELECT * FROM some_table"
    )
