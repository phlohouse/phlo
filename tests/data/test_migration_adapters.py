"""Tests for migration source adapters.

Covers CsvSourceAdapter config validation (required path, no query/table
selectors), chunked reads and row-count estimation, and registry-based
resolution that returns None for unknown source types.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from phlo.migrations.adapters import CsvSourceAdapter, resolve_source_adapter
from phlo.migrations.specs import MigrationSource


def _csv_source(path: str | None = None, **kwargs: Any) -> MigrationSource:
    return MigrationSource(type="csv", path=path, **kwargs)


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class TestCsvSourceAdapter:
    def test_csv_source_type(self) -> None:
        assert CsvSourceAdapter().source_type == "csv"

    def test_validate_missing_path(self) -> None:
        errors = CsvSourceAdapter().validate_config(_csv_source(path=None))
        assert any("path" in e for e in errors)

    def test_validate_nonexistent_file(self, tmp_path: Path) -> None:
        errors = CsvSourceAdapter().validate_config(_csv_source(path=str(tmp_path / "nope.csv")))
        assert any("not found" in e for e in errors)

    def test_validate_with_query_unsupported(self, tmp_path: Path) -> None:
        csv_file = _write_csv(tmp_path / "data.csv", ["a,b", "1,2"])
        errors = CsvSourceAdapter().validate_config(
            _csv_source(path=str(csv_file), query="SELECT 1")
        )
        assert any("query" in e for e in errors)

    def test_validate_with_table_unsupported(self, tmp_path: Path) -> None:
        csv_file = _write_csv(tmp_path / "data.csv", ["a,b", "1,2"])
        errors = CsvSourceAdapter().validate_config(_csv_source(path=str(csv_file), table="t"))
        assert any("table" in e for e in errors)

    def test_validate_valid_csv(self, tmp_path: Path) -> None:
        csv_file = _write_csv(tmp_path / "data.csv", ["a,b", "1,2"])
        errors = CsvSourceAdapter().validate_config(_csv_source(path=str(csv_file)))
        assert errors == []

    def test_read_chunks_single_chunk(self, tmp_path: Path) -> None:
        csv_file = _write_csv(tmp_path / "data.csv", ["a,b", "1,2", "3,4"])
        chunks = list(
            CsvSourceAdapter().read_chunks(_csv_source(path=str(csv_file)), chunk_size=50_000)
        )
        assert len(chunks) == 1
        assert chunks[0] == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]

    def test_read_chunks_multiple_chunks(self, tmp_path: Path) -> None:
        csv_file = _write_csv(tmp_path / "data.csv", ["x", "1", "2", "3", "4", "5"])
        chunks = list(CsvSourceAdapter().read_chunks(_csv_source(path=str(csv_file)), chunk_size=2))
        assert len(chunks) == 3
        assert len(chunks[0]) == 2
        assert len(chunks[1]) == 2
        assert len(chunks[2]) == 1

    def test_read_chunks_missing_path_raises(self) -> None:
        with pytest.raises(ValueError, match="path"):
            list(CsvSourceAdapter().read_chunks(_csv_source(path=None)))

    def test_estimate_row_count(self, tmp_path: Path) -> None:
        csv_file = _write_csv(tmp_path / "data.csv", ["a", "1", "2", "3"])
        assert CsvSourceAdapter().estimate_row_count(_csv_source(path=str(csv_file))) == 3

    def test_estimate_row_count_missing_path(self) -> None:
        assert CsvSourceAdapter().estimate_row_count(_csv_source(path=None)) is None


class _FakeRegistry:
    def list(self, family: str):
        assert family == "data_migration_source"
        return []


class TestResolveSourceAdapter:
    def test_resolve_csv_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("phlo.migrations.adapters.get_capability_registry", _FakeRegistry)
        adapter = resolve_source_adapter("csv")
        assert adapter is not None
        assert adapter.source_type == "csv"

    def test_resolve_unknown_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("phlo.migrations.adapters.get_capability_registry", _FakeRegistry)
        assert resolve_source_adapter("unknown") is None
