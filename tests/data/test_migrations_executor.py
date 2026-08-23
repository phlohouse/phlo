"""Regression tests for migration executor edge cases.

Covers dry-run override semantics, column-mapping collapse rejection,
quality-validation plumbing, overwrite support checks, temp-file cleanup
on write failure, stable event correlation, and multi-store guidance.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from phlo.hooks.events import DataMigrationEvent
from phlo.migrations import executor as migration_executor
from phlo.migrations.executor import (
    MigrationExecutionError,
    MigrationExecutor,
    _apply_column_mapping,
    _stage_chunk_parquet,
    _validate_quality_chunk,
    _write_chunk_to_table_store,
)
from phlo.migrations.specs import (
    MigrationDestination,
    MigrationOptions,
    MigrationSource,
    MigrationSpec,
)
from tests.helpers import RecordingBus


class _FakeAdapter:
    def validate_config(self, source: MigrationSource) -> list[str]:
        return []

    def read_chunks(self, source: MigrationSource, *, chunk_size: int = 50_000):
        yield []

    def estimate_row_count(self, source: MigrationSource) -> int | None:
        return 0


class _FakeRegistry:
    def list_table_stores(self) -> list[object]:
        return []


def _spec(*, write_mode: str = "append", dry_run: bool = False) -> MigrationSpec:
    return MigrationSpec(
        name="demo",
        version="1.0",
        description="demo",
        source=MigrationSource(type="csv", path="input.csv"),
        destination=MigrationDestination(table="warehouse.demo", write_mode=write_mode),
        options=MigrationOptions(dry_run=dry_run),
    )


def test_validate_respects_dry_run_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run override bypasses table-store requirement during validation."""
    monkeypatch.setattr(migration_executor, "discover_capabilities", lambda: None)
    monkeypatch.setattr(migration_executor, "resolve_source_adapter", lambda _: _FakeAdapter())
    monkeypatch.setattr(migration_executor, "resolve_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration_executor, "configured_capability_name", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(migration_executor, "list_capabilities", lambda *_args, **_kwargs: [])

    executor = MigrationExecutor()
    errors_without_override = executor.validate(_spec(dry_run=False))
    assert any("No table store registered" in error for error in errors_without_override)

    errors_with_override = executor.validate(_spec(dry_run=False), dry_run_override=True)
    assert errors_with_override == []


def test_apply_column_mapping_rejects_collapsed_destination() -> None:
    """Mapping collisions should fail instead of silently overwriting row values."""
    rows = [{"id": 1, "legacy_id": 2}]

    with pytest.raises(MigrationExecutionError, match="multiple source columns"):
        _apply_column_mapping(rows, {"legacy_id": "id"})


def test_apply_column_mapping_preserves_unmapped_columns() -> None:
    rows = [{"id": 1, "full_name": "Ada"}]

    assert _apply_column_mapping(rows, {"full_name": "name"}) == [{"id": 1, "name": "Ada"}]


def test_validate_quality_chunk_requires_validate_method() -> None:
    with pytest.raises(MigrationExecutionError, match="does not expose a 'validate' method"):
        _validate_quality_chunk(object(), [{"id": 1}])


def test_validate_quality_chunk_passes_dataframe_to_schema() -> None:
    class _Schema:
        observed_columns: list[str] | None = None
        observed_rows: int | None = None

        @classmethod
        def validate(cls, frame) -> None:  # type: ignore[no-untyped-def]
            cls.observed_columns = list(frame.columns)
            cls.observed_rows = len(frame)

    _validate_quality_chunk(_Schema, [{"id": 1, "name": "Ada"}])

    assert _Schema.observed_columns == ["id", "name"]
    assert _Schema.observed_rows == 1


def test_validate_quality_chunk_propagates_schema_errors() -> None:
    class _Schema:
        @staticmethod
        def validate(frame) -> None:  # type: ignore[no-untyped-def]
            raise ValueError("invalid chunk")

    with pytest.raises(ValueError, match="invalid chunk"):
        _validate_quality_chunk(_Schema, [{"id": 1}])


def test_overwrite_mode_requires_overwrite_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Overwrite write mode fails fast when provider lacks overwrite support."""
    staged_path = tmp_path / "chunk.parquet"
    staged_path.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(migration_executor, "_stage_chunk_parquet", lambda _: staged_path)

    class AppendOnlyStore:
        def __init__(self) -> None:
            self.append_calls = 0

        def append_parquet(self, *, table_name: str, data_path: Path) -> None:
            self.append_calls += 1

    store = AppendOnlyStore()
    with pytest.raises(MigrationExecutionError, match="requires table store support"):
        _write_chunk_to_table_store(
            table_store=store,
            table_name="warehouse.demo",
            write_mode="overwrite",
            unique_key=None,
            chunk=[{"id": "1"}],
            first_chunk=True,
        )

    assert store.append_calls == 0
    assert not staged_path.exists()


def test_stage_chunk_parquet_cleans_temp_file_on_write_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Temporary parquet path is removed when write_table fails."""
    staged_path = tmp_path / "broken.parquet"

    class _TmpFile:
        def __init__(self, name: str) -> None:
            self.name = name

    class _TmpContext:
        def __enter__(self) -> _TmpFile:
            staged_path.write_text("tmp", encoding="utf-8")
            return _TmpFile(str(staged_path))

        def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(
        migration_executor.tempfile,
        "NamedTemporaryFile",
        lambda **_: _TmpContext(),
    )

    fake_pa = types.ModuleType("pyarrow")

    class _FakeTable:
        @staticmethod
        def from_pylist(rows):  # type: ignore[no-untyped-def]
            return rows

    fake_pa.Table = _FakeTable  # type: ignore[attr-defined]
    fake_pq = types.ModuleType("pyarrow.parquet")

    def _raise_write_error(table, path):  # type: ignore[no-untyped-def]
        raise RuntimeError("write failed")

    fake_pq.write_table = _raise_write_error  # type: ignore[attr-defined]

    monkeypatch.setitem(__import__("sys").modules, "pyarrow", fake_pa)
    monkeypatch.setitem(__import__("sys").modules, "pyarrow.parquet", fake_pq)

    with pytest.raises(RuntimeError, match="write failed"):
        _stage_chunk_parquet([{"id": "1"}])

    assert not staged_path.exists()


def test_execute_emits_stable_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migration execution reuses one request correlation across emitted events."""

    class _Adapter:
        def validate_config(self, source: MigrationSource) -> list[str]:
            return []

        def estimate_row_count(self, source: MigrationSource) -> int | None:
            return 2

        def read_chunks(self, source: MigrationSource, *, chunk_size: int = 50_000):
            yield [{"id": "1"}]
            yield [{"id": "2"}]

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)
    monkeypatch.setattr(migration_executor, "discover_capabilities", lambda: None)
    monkeypatch.setattr(migration_executor, "resolve_source_adapter", lambda _: _Adapter())
    monkeypatch.setattr(migration_executor, "_append_history", lambda result: None)

    result = MigrationExecutor().execute(_spec(dry_run=True))

    assert result.status == "dry_run"
    migration_events = [event for event in bus.events if isinstance(event, DataMigrationEvent)]
    assert migration_events
    request_ids = {event.correlation.request_id for event in migration_events}
    assert len(request_ids) == 1
    assert request_ids != {None}
    assert {event.correlation.asset_key for event in migration_events} == {"warehouse.demo"}


def test_execute_requires_configured_default_when_multiple_table_stores_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple table stores should fail with deterministic guidance."""

    monkeypatch.setattr(migration_executor, "discover_capabilities", lambda: None)
    monkeypatch.setattr(migration_executor, "resolve_source_adapter", lambda _: _FakeAdapter())
    monkeypatch.setattr(migration_executor, "list_capabilities", lambda _: ["iceberg", "delta"])
    monkeypatch.setattr(migration_executor, "resolve_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration_executor,
        "configured_capability_name",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(MigrationExecutionError, match="Multiple table_store providers"):
        MigrationExecutor().execute(_spec(dry_run=False))


def test_validate_requires_configured_default_when_multiple_table_stores_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation should fail when multiple table stores exist without a configured default."""

    monkeypatch.setattr(migration_executor, "discover_capabilities", lambda: None)
    monkeypatch.setattr(migration_executor, "resolve_source_adapter", lambda _: _FakeAdapter())
    monkeypatch.setattr(migration_executor, "resolve_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration_executor,
        "configured_capability_name",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(migration_executor, "list_capabilities", lambda _: ["iceberg", "delta"])

    errors = MigrationExecutor().validate(_spec(dry_run=False))

    assert any("Multiple table_store providers are registered" in error for error in errors)
