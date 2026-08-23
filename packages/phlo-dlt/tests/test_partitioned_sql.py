"""Tests for partitioned SQL ingestion helpers.

Uses fake cursor/connection doubles to verify window binding, batch fetching,
row normalization, parameter merging with default overrides, empty-result
handling, missing-template reporting, and packaged SQL template loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import importlib
from pathlib import Path
import sys
from typing import Any

import pytest

import phlo_dlt
from phlo_dlt.partitioned_sql import (
    PartitionWindow,
    PartitionedSqlConfig,
    load_sql_template,
    run_partitioned_sql,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]], columns: list[str]) -> None:
        self._rows = list(rows)
        self.description = [(column,) for column in columns]
        self.executions: list[tuple[str, dict[str, Any]]] = []
        self.fetch_sizes: list[int] = []
        self.closed = False

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.executions.append((sql, params))

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        self.fetch_sizes.append(size)
        batch = self._rows[:size]
        self._rows = self._rows[size:]
        return batch

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeConnector:
    connection: FakeConnection

    def __call__(self) -> FakeConnection:
        return self.connection


def _write_template(tmp_path: Path, sql: str = "select * from events") -> Path:
    template = tmp_path / "events.sql"
    template.write_text(sql, encoding="utf-8")
    return template


def test_run_partitioned_sql_binds_window_fetches_batches_and_normalizes_rows(
    tmp_path: Path,
) -> None:
    cursor = FakeCursor(
        rows=[(1, "Alice"), (2, "Bob"), (3, "Cara")],
        columns=["User ID", "Display-Name"],
    )
    connection = FakeConnection(cursor)
    window = PartitionWindow(
        partition_key="2026-06-04",
        start=date(2026, 6, 4),
        end=date(2026, 6, 5),
    )
    config = PartitionedSqlConfig(
        sql_template_path=_write_template(tmp_path),
        fetch_size=2,
        row_defaults={"source_system": "crm"},
    )

    rows = list(run_partitioned_sql(config, window=window, connect=FakeConnector(connection)))

    assert rows == [
        {
            "source_system": "crm",
            "partition_date": "2026-06-04",
            "user_id": 1,
            "display_name": "Alice",
        },
        {
            "source_system": "crm",
            "partition_date": "2026-06-04",
            "user_id": 2,
            "display_name": "Bob",
        },
        {
            "source_system": "crm",
            "partition_date": "2026-06-04",
            "user_id": 3,
            "display_name": "Cara",
        },
    ]
    assert cursor.executions == [
        (
            "select * from events",
            {
                "partition_key": "2026-06-04",
                "partition_start": date(2026, 6, 4),
                "partition_end": date(2026, 6, 5),
            },
        )
    ]
    assert cursor.fetch_sizes == [2, 2, 2]
    assert cursor.closed is True
    assert connection.closed is True


def test_run_partitioned_sql_merges_custom_params_and_allows_default_override(
    tmp_path: Path,
) -> None:
    cursor = FakeCursor(rows=[("external",)], columns=["Source System"])
    config = PartitionedSqlConfig(
        sql_template_path=_write_template(tmp_path),
        row_defaults={"source_system": "static"},
        params={"tenant_id": "tenant-1"},
    )

    rows = list(
        run_partitioned_sql(
            config,
            window=PartitionWindow("2026-06-04", "2026-06-04", "2026-06-05"),
            connect=FakeConnector(FakeConnection(cursor)),
        )
    )

    assert rows == [{"source_system": "external", "partition_date": "2026-06-04"}]
    assert cursor.executions[0][1]["tenant_id"] == "tenant-1"


def test_run_partitioned_sql_yields_no_rows_for_empty_result(tmp_path: Path) -> None:
    cursor = FakeCursor(rows=[], columns=["id"])
    config = PartitionedSqlConfig(sql_template_path=_write_template(tmp_path))

    rows = list(
        run_partitioned_sql(
            config,
            window=PartitionWindow("2026-06-04", "2026-06-04", "2026-06-05"),
            connect=FakeConnector(FakeConnection(cursor)),
        )
    )

    assert rows == []
    assert cursor.fetch_sizes == [1000]


def test_run_partitioned_sql_reports_missing_template(tmp_path: Path) -> None:
    config = PartitionedSqlConfig(sql_template_path=tmp_path / "missing.sql")

    with pytest.raises(FileNotFoundError, match="SQL template not found"):
        list(
            run_partitioned_sql(
                config,
                window=PartitionWindow("2026-06-04", "2026-06-04", "2026-06-05"),
                connect=FakeConnector(FakeConnection(FakeCursor([], ["id"]))),
            )
        )


def test_load_sql_template_reads_packaged_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "workflow_sql"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "events.sql").write_text("select 1", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    sql = load_sql_template(
        PartitionedSqlConfig(
            sql_template_package="workflow_sql",
            sql_template_name="events.sql",
        )
    )

    assert sql == "select 1"
    sys.modules.pop("workflow_sql", None)


def test_partitioned_sql_helpers_are_exported_from_phlo_dlt() -> None:
    assert phlo_dlt.PartitionWindow is PartitionWindow
    assert phlo_dlt.PartitionedSqlConfig is PartitionedSqlConfig
    assert callable(phlo_dlt.run_partitioned_sql)
