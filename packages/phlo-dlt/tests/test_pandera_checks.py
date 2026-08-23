"""Tests for Pandera parquet contract helpers.

Contract evaluation combines parquet file sets before validating,
backfills missing nullable columns using the schema's dtype (e.g.
Int64, datetime64[ns]), and never mutates the input DataFrame.
"""

from __future__ import annotations

import pandas as pd
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
