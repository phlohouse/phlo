"""Bounded Parquet validation shared by ingestion and quality providers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd

ValidationMode = Literal["materialized", "full", "sample"]


class ContractEvaluation(Protocol):
    """The summary consumed from a provider's Pandera adapter."""

    @property
    def passed(self) -> bool: ...
    @property
    def failed_count(self) -> int: ...
    @property
    def total_count(self) -> int: ...
    @property
    def sample(self) -> list[dict[str, Any]]: ...
    @property
    def error(self) -> str | None: ...


def validate_batch_contract(schema: Any) -> None:
    """Reject contracts whose meaning changes when the dataset is split."""
    if schema.checks or schema.unique or schema.index is not None or schema.parsers:
        raise ValueError(
            "Bounded full validation requires a row-local contract; use materialized mode for dataset checks, uniqueness, indexes or parsers"
        )
    for column in schema.columns.values():
        if column.unique or column.parsers or column.regex:
            raise ValueError(
                "Bounded full validation does not support unique, regex or parsed columns; use materialized mode"
            )
        for check in column.checks:
            # determined_by_unique is Pandera's declaration that a check can
            # evaluate each distinct value independently of the other rows.
            if (
                check.groupby
                or check.n_failure_cases is not None
                or not (check.element_wise or check.determined_by_unique)
            ):
                raise ValueError(
                    "Bounded full validation requires element-wise or row-local built-in checks; use materialized mode"
                )


def parquet_validation_frames(
    paths: Sequence[Path],
    *,
    mode: ValidationMode,
    batch_size: int,
    sample_size: int,
) -> Iterator[pd.DataFrame]:
    """Yield full bounded batches or a deterministic prefix sample across files."""
    import pyarrow.parquet as pq

    if mode not in {"materialized", "full", "sample"}:
        raise ValueError(f"Unknown validation mode: {mode}")
    if batch_size <= 0 or sample_size <= 0:
        raise ValueError("Validation batch_size and sample_size must be positive")
    if not paths:
        raise FileNotFoundError("Missing parquet_paths in ingestion metadata")
    if mode == "materialized":
        yield pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
        return
    offset = 0
    sample_frames = []
    empty = None
    for path in paths:
        parquet = pq.ParquetFile(path)
        if empty is None:
            empty = parquet.schema_arrow.empty_table().to_pandas()
        for batch in parquet.iter_batches(
            batch_size=min(batch_size, sample_size) if mode == "sample" else batch_size
        ):
            frame = batch.to_pandas()
            frame.index = pd.RangeIndex(offset, offset + len(frame))
            if mode == "sample":
                frame = frame.iloc[: sample_size - offset]
                sample_frames.append(frame)
            else:
                yield frame
            offset += len(frame)
            if mode == "sample" and offset == sample_size:
                yield pd.concat(sample_frames)
                return
    if mode == "sample" and sample_frames:
        yield pd.concat(sample_frames)
    elif offset == 0:
        assert empty is not None
        yield empty


def evaluate_validation_frames(
    frames: Iterator[pd.DataFrame],
    evaluate: Callable[[pd.DataFrame], ContractEvaluation],
) -> tuple[bool, int, int, list[dict[str, Any]], str | None]:
    """Retain exact counts and a bounded failure sample across validation batches."""
    passed, failed, total = True, 0, 0
    sample: list[dict[str, Any]] = []
    error = None
    for frame in frames:
        result = evaluate(frame)
        passed = passed and result.passed
        failed += result.failed_count
        total += result.total_count
        sample.extend(result.sample[: 20 - len(sample)])
        if error is None:
            error = result.error
    return passed, failed, total, sample, error
