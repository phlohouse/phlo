"""Tests for Pandera parquet contract helpers.

Contract evaluation combines parquet file sets before validating,
backfills missing nullable columns using the schema's dtype (e.g.
Int64, datetime64[ns]), and never mutates the input DataFrame.
"""

from __future__ import annotations

import pandas as pd
import pytest
from pandera.pandas import Field
from pandera.pandas import DataFrameModel
from pandera.typing import Series  # type: ignore[possibly-missing-import]

from phlo_dlt.pandera_checks import (
    _nullable_series_for_schema_column,
    evaluate_pandera_contract,
    evaluate_pandera_contract_parquet_files,
)


class MultiFileSchema(DataFrameModel):
    """Schema used to validate combined parquet staging outputs."""

    id: Series[int]
    name: Series[str]


class NullableFieldSchema(DataFrameModel):
    """Schema used to validate widening changes with nullable columns."""

    id: Series[int]
    habitat: Series[str] = Field(nullable=True)


class NullableTypedFieldSchema(DataFrameModel):
    """Schema used to validate typed nullable column backfills."""

    id: Series[int]
    score: Series[int] = Field(nullable=True)
    observed_at: Series[pd.Timestamp] = Field(nullable=True)


def test_evaluate_pandera_contract_parquet_files_combines_file_set(tmp_path) -> None:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    pd.DataFrame([{"id": 1, "name": "alpha"}]).to_parquet(left_path)
    pd.DataFrame([{"id": 2, "name": "beta"}]).to_parquet(right_path)

    evaluation = evaluate_pandera_contract_parquet_files(
        [left_path, right_path],
        schema_class=MultiFileSchema,
    )

    assert evaluation.passed is True
    assert evaluation.total_count == 2
    assert evaluation.failed_count == 0


def test_evaluate_pandera_contract_backfills_missing_nullable_columns() -> None:
    evaluation = evaluate_pandera_contract(
        pd.DataFrame([{"id": 1}]),
        schema_class=NullableFieldSchema,
    )

    assert evaluation.passed is True
    assert evaluation.total_count == 1
    assert evaluation.failed_count == 0


def test_evaluate_pandera_contract_backfills_missing_nullable_columns_with_schema_dtype() -> None:
    evaluation = evaluate_pandera_contract(
        pd.DataFrame([{"id": 1}]),
        schema_class=NullableTypedFieldSchema,
    )

    assert evaluation.passed is True
    assert evaluation.total_count == 1
    assert evaluation.failed_count == 0


def test_nullable_series_for_schema_column_uses_schema_dtype() -> None:
    schema = NullableTypedFieldSchema.to_schema()

    score_series = _nullable_series_for_schema_column(schema.columns["score"], size=2)
    observed_series = _nullable_series_for_schema_column(schema.columns["observed_at"], size=2)

    assert str(score_series.dtype) == "Int64"
    assert str(observed_series.dtype) == "datetime64[ns]"
    assert score_series.isna().all()
    assert observed_series.isna().all()


def test_evaluate_pandera_contract_does_not_mutate_input_dataframe() -> None:
    df = pd.DataFrame([{"id": 1}])

    evaluate_pandera_contract(
        df,
        schema_class=NullableFieldSchema,
    )

    assert list(df.columns) == ["id"]


def test_full_batches_find_late_failures_and_sample_stays_explicit(tmp_path) -> None:
    class PositiveSchema(DataFrameModel):
        id: Series[int] = Field(gt=0)

    paths = [tmp_path / "first.parquet", tmp_path / "second.parquet"]
    pd.DataFrame({"id": [1, 2, 3]}).to_parquet(paths[0])
    pd.DataFrame({"id": [4, -5, 6, -7]}).to_parquet(paths[1])
    full = evaluate_pandera_contract_parquet_files(
        paths,
        schema_class=PositiveSchema,
        mode="full",
        batch_size=2,
    )
    materialized = evaluate_pandera_contract_parquet_files(paths, schema_class=PositiveSchema)
    sample = evaluate_pandera_contract_parquet_files(
        paths,
        schema_class=PositiveSchema,
        mode="sample",
        batch_size=2,
        sample_size=4,
    )
    assert not full.passed
    assert full.failed_count == materialized.failed_count == 2
    assert full.total_count == materialized.total_count == 7
    assert full.sample == materialized.sample
    assert sample.passed and sample.total_count == 4


def test_full_validation_never_silently_weakens_global_uniqueness(tmp_path) -> None:
    class UniqueSchema(DataFrameModel):
        id: Series[int] = Field(unique=True)

    path = tmp_path / "duplicates.parquet"
    pd.DataFrame({"id": [1, 2, 1]}).to_parquet(path)
    assert not evaluate_pandera_contract_parquet_files([path], schema_class=UniqueSchema).passed
    with pytest.raises(ValueError, match="unique"):
        evaluate_pandera_contract_parquet_files(
            [path],
            schema_class=UniqueSchema,
            mode="full",
            batch_size=2,
        )
