"""Tests for contract-coverage detection and decorator ergonomics."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.typing import Series
from phlo.exceptions import PhloConfigError

from phlo_dlt.contract_coverage import (
    declared_contract_columns,
    detect_dropped_source_columns,
)
from phlo_dlt.decorator import clear_ingestion_assets, get_ingestion_assets, phlo_ingestion


class WideSchema(pa.DataFrameModel):
    """Contract that intentionally declares a subset of staged columns."""

    id: Series[str]
    payload: Series[str]

    class Config:
        strict = False


def _write_parquet(path: Path) -> None:
    pd.DataFrame(
        {
            "id": ["a", "b"],
            "payload": ["x", "y"],
            "extra_source_column": [1, 2],
            "_dlt_load_id": ["l1", "l2"],
            "_phlo_row_id": ["r1", "r2"],
        }
    ).to_parquet(path, index=False)


def test_detect_dropped_columns_ignores_internal_bookkeeping(tmp_path: Path) -> None:
    parquet = tmp_path / "staged.parquet"
    _write_parquet(parquet)
    dropped = detect_dropped_source_columns([parquet], WideSchema)
    assert dropped == ["extra_source_column"]


def test_declared_contract_columns_matches_model(tmp_path: Path) -> None:
    assert declared_contract_columns(WideSchema) == {"id", "payload"}
    parquet = tmp_path / "clean.parquet"
    pd.DataFrame({"id": ["a"], "payload": ["x"], "_dlt_load_id": ["l1"]}).to_parquet(
        parquet, index=False
    )
    assert detect_dropped_source_columns([parquet], WideSchema) == []


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_ingestion_assets()
    yield
    clear_ingestion_assets()


class RefSchema(pa.DataFrameModel):
    code: Series[str] = pa.Field(unique=True)

    class Config:
        strict = False


def noop_quality_check(frame: pd.DataFrame) -> str | None:
    """Domain check that always passes."""
    return None


def failing_quality_check(frame: pd.DataFrame) -> str | None:
    """Domain check that always reports a violation."""
    return "synthetic violation"


class _Runtime:
    """Runtime stub mirroring the orchestrator: partition access returns None."""

    run_id = "test-run"

    def __init__(self, partition_key: str | None = "2026-08-17") -> None:
        self._partition_key = partition_key

    @property
    def partition_key(self) -> str | None:
        return self._partition_key

    class logger:  # noqa: N801 - attribute namespace stub
        def info(self, *args, **kwargs) -> None: ...

        warning = info
        error = info


class _StubIngester:
    """Ingestion stub: skips DLT and table-store machinery in run-level tests."""

    staged_frame: pd.DataFrame

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def run_ingestion(self, partition_key, parameters):  # noqa: ANN001, ARG002
        staged = tmp_parquet_path()
        self.staged_frame.to_parquet(staged, index=False)
        return SimpleNamespace(
            status="success",
            rows_inserted=len(self.staged_frame),
            rows_deleted=0,
            metadata={"parquet_paths": [str(staged)], "parquet_path": str(staged)},
        )


_STAGED_PARQUET: Path | None = None


def tmp_parquet_path() -> Path:
    assert _STAGED_PARQUET is not None
    return _STAGED_PARQUET


@pytest.fixture()
def stub_ingester(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    global _STAGED_PARQUET
    frame = pd.DataFrame({"code": ["A", "B"]})
    _STAGED_PARQUET = tmp_path / "staged.parquet"
    _StubIngester.staged_frame = frame
    monkeypatch.setattr(
        "phlo_dlt.decorator._resolve_table_store_capability",
        lambda runtime: (SimpleNamespace(), "test-store"),
    )
    monkeypatch.setattr("phlo_dlt.executor.DltIngester", _StubIngester)
    return frame


def test_partitioned_false_skips_partition_requirement(
    stub_ingester: pd.DataFrame,
) -> None:
    runtime = _Runtime(partition_key=None)

    @phlo_ingestion(
        table_name="ref_codes",
        unique_key="code",
        group="reference",
        validation_schema=RefSchema,
        partitioned=False,
    )
    def load_ref(partition_date: str):
        assert partition_date == ""
        return [{"code": "A"}]

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    spec = assets["dlt_ref_codes"]
    assert spec.partitions is None
    results = list(spec.run.fn(runtime))
    materializations = [
        result for result in results if type(result).__name__ == "MaterializeResult"
    ]
    assert materializations, "expected a materialization result"


def test_partitioned_asset_still_requires_partition_key() -> None:
    runtime = _Runtime(partition_key=None)

    @phlo_ingestion(
        table_name="dated",
        unique_key="code",
        group="events",
        validation_schema=RefSchema,
    )
    def load_dated(partition_date: str):
        return [{"code": "A"}]

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    spec = assets["dlt_dated"]
    assert spec.partitions is not None
    with pytest.raises(PhloConfigError, match="Missing partition key"):
        list(spec.run.fn(runtime))


def test_failing_quality_check_blocks_strict_run(
    stub_ingester: pd.DataFrame,
) -> None:
    observed: list[pd.DataFrame] = []

    def breach(frame: pd.DataFrame) -> str | None:
        observed.append(frame)
        return "paid exceeds allowed"

    @phlo_ingestion(
        table_name="guarded",
        unique_key="code",
        group="guarded",
        validation_schema=RefSchema,
        quality_checks=[breach],
    )
    def load_guarded(partition_date: str):
        del partition_date
        return [{"code": "A"}]

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    spec = assets["dlt_guarded"]
    assert "quality_breach" in [check.name for check in spec.checks]
    assert all(check.blocking for check in spec.checks)
    collected: list[object] = []
    with pytest.raises(RuntimeError, match="Domain quality check failed"):
        for item in spec.run.fn(_Runtime()):
            collected.append(item)
    quality_results = [
        item for item in collected if getattr(item, "check_name", "").startswith("quality_")
    ]
    assert [item.passed for item in quality_results] == [False]
    assert len(observed) == 1 and observed[0].equals(stub_ingester)


def test_passing_quality_check_yields_passed_result(
    stub_ingester: pd.DataFrame,
) -> None:
    @phlo_ingestion(
        table_name="ok_guarded",
        unique_key="code",
        group="guarded",
        validation_schema=RefSchema,
        quality_checks=[noop_quality_check],
    )
    def load_ok(partition_date: str):
        del partition_date
        return [{"code": "A"}]

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    spec = assets["dlt_ok_guarded"]
    results = list(spec.run.fn(_Runtime()))
    quality_results = [
        item for item in results if getattr(item, "check_name", "").startswith("quality_")
    ]
    assert [item.passed for item in quality_results] == [True]


def test_failing_quality_check_warns_in_non_strict_mode(
    stub_ingester: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @phlo_ingestion(
        table_name="lenient_guarded",
        unique_key="code",
        group="guarded",
        validation_schema=RefSchema,
        quality_checks=[failing_quality_check],
        strict_validation=False,
    )
    def load_lenient(partition_date: str):
        del partition_date
        return [{"code": "A"}]

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    spec = assets["dlt_lenient_guarded"]
    results = list(spec.run.fn(_Runtime()))
    quality_results = [
        item for item in results if getattr(item, "check_name", "").startswith("quality_")
    ]
    assert [item.passed for item in quality_results] == [False]


def test_failing_quality_check_raises_in_strict_mode(
    stub_ingester: pd.DataFrame,
) -> None:
    @phlo_ingestion(
        table_name="strict_guarded",
        unique_key="code",
        group="guarded",
        validation_schema=RefSchema,
        quality_checks=[failing_quality_check],
    )
    def load_strict(partition_date: str):
        del partition_date
        return [{"code": "A"}]

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    spec = assets["dlt_strict_guarded"]
    collected: list[object] = []
    with pytest.raises(RuntimeError, match="Domain quality check failed"):
        for item in spec.run.fn(_Runtime()):
            collected.append(item)
    quality_results = [
        item for item in collected if getattr(item, "check_name", "").startswith("quality_")
    ]
    assert [item.passed for item in quality_results] == [False]
