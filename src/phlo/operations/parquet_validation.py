"""Bounded Parquet validation shared by ingestion and quality providers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
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


def _validate_batch_contract(schema: Any) -> None:
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


def _parquet_validation_frames(
    paths: Path | Sequence[Path],
    *,
    mode: ValidationMode,
    batch_size: int,
    sample_size: int,
) -> Iterator[pd.DataFrame]:
    """Yield full bounded batches or a deterministic prefix sample across files."""
    import pandas as pd
    import pyarrow.parquet as pq

    if mode == "materialized":
        if isinstance(paths, Path):
            yield pd.read_parquet(paths)
        else:
            yield pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
        return
    if isinstance(paths, Path):
        paths = [paths]
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


def evaluate_parquet_contract(
    paths: Path | Sequence[Path],
    *,
    schema_class: type[Any],
    evaluate: Callable[[pd.DataFrame], ContractEvaluation],
    mode: ValidationMode = "materialized",
    batch_size: int = 65_536,
    sample_size: int = 1_000,
) -> tuple[bool, int, int, list[dict[str, Any]], str | None]:
    """Validate staged Parquet with a provider's DataFrame evaluator.

    Full mode rejects non-row-local contracts before reading, then evaluates
    at most ``batch_size`` rows at once. Sample mode evaluates a deterministic
    prefix of at most ``sample_size`` rows in path order. Materialized mode
    evaluates the entire dataset, preserving a single Path's stored index
    or resetting the index when combining a sequence of paths.

    Sizes must be positive except for a single materialized Path, which does
    not use them. Counts include every evaluated failure and row;
    only the first 20 failure cases and first error are retained. Read and
    evaluator exceptions propagate to the provider.
    """
    if not paths:
        raise FileNotFoundError("Missing parquet_paths in ingestion metadata")
    if mode == "full":
        _validate_batch_contract(schema_class.to_schema())
    if mode not in {"materialized", "full", "sample"}:
        raise ValueError(f"Unknown validation mode: {mode}")
    if (mode != "materialized" or not isinstance(paths, Path)) and (
        batch_size <= 0 or sample_size <= 0
    ):
        raise ValueError("Validation batch_size and sample_size must be positive")
    frames = _parquet_validation_frames(
        paths, mode=mode, batch_size=batch_size, sample_size=sample_size
    )
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
