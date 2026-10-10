"""Tests that staged domain-quality checks read the staged Parquet once per run.

Every check must see the full staged rows, a read failure must still reject
each check, and one check's in-place edits must not leak into the next.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from phlo_dlt.executor import StagedFrameLoader, _evaluate_domain_quality_checks


@pytest.fixture
def staged_paths(tmp_path: Path) -> list[Path]:
    paths = []
    for index in range(3):
        path = tmp_path / f"part-{index}.parquet"
        pd.DataFrame({"value": [index * 10, index * 10 + 1]}).to_parquet(path)
        paths.append(path)
    return paths


@pytest.fixture
def read_counter(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    reads: list[Path] = []
    real_read_parquet = pd.read_parquet

    def _counting_read_parquet(path, *args, **kwargs):
        reads.append(Path(path))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr("phlo_dlt.executor.pd.read_parquet", _counting_read_parquet)
    return reads


def test_checks_read_each_staged_file_once(
    staged_paths: list[Path], read_counter: list[Path]
) -> None:
    def _check(frame: pd.DataFrame) -> str | None:
        return None if len(frame) == 6 else f"saw {len(frame)} rows"

    evaluations = _evaluate_domain_quality_checks(
        parquet_paths=staged_paths,
        quality_checks=[_check] * 5,
    )

    assert [evaluation.passed for evaluation in evaluations] == [True] * 5
    assert sorted(read_counter) == sorted(staged_paths)


def test_no_checks_reads_nothing(staged_paths: list[Path], read_counter: list[Path]) -> None:
    assert _evaluate_domain_quality_checks(parquet_paths=staged_paths, quality_checks=[]) == ()
    assert read_counter == []


def test_read_failure_rejects_every_check_without_rereading(
    tmp_path: Path, read_counter: list[Path]
) -> None:
    missing = tmp_path / "missing.parquet"

    evaluations = _evaluate_domain_quality_checks(
        parquet_paths=[missing],
        quality_checks=[lambda _frame: None, lambda _frame: None],
    )

    assert [evaluation.passed for evaluation in evaluations] == [False, False]
    assert all(
        (evaluation.violation or "").startswith("quality check raised:")
        for evaluation in evaluations
    )
    assert read_counter == [missing]


@pytest.mark.parametrize("copy_on_write", [True, False])
def test_check_mutation_does_not_leak_into_next_check(
    staged_paths: list[Path], monkeypatch: pytest.MonkeyPatch, copy_on_write: bool
) -> None:
    monkeypatch.setattr("phlo_dlt.executor._copy_on_write_enabled", lambda: copy_on_write)

    def _mutating_check(frame: pd.DataFrame) -> str | None:
        frame["value"] = -1
        frame.drop(frame.index, inplace=True)
        return None

    def _original_rows_check(frame: pd.DataFrame) -> str | None:
        if len(frame) != 6 or (frame["value"] < 0).any():
            return "staged rows were modified by an earlier check"
        return None

    evaluations = _evaluate_domain_quality_checks(
        parquet_paths=staged_paths,
        quality_checks=[_mutating_check, _original_rows_check],
    )

    assert [evaluation.violation for evaluation in evaluations] == [None, None]


def test_loader_is_lazy(staged_paths: list[Path], read_counter: list[Path]) -> None:
    loader = StagedFrameLoader(staged_paths)
    assert read_counter == []
    assert len(loader.load()) == 6
    assert len(loader.load()) == 6
    assert len(read_counter) == len(staged_paths)
