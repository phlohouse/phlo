"""Shared Parquet evaluation preserves dataset semantics and bounded reads."""

from pathlib import Path

import pandas as pd
import pytest
from pandera.pandas import DataFrameModel, Field, dataframe_check
from pandera.typing import Index, Series
from phlo_dlt.pandera_checks import (
    evaluate_pandera_contract,
    evaluate_pandera_contract_parquet_files,
)
from phlo_dlt.pandera_checks import evaluate_pandera_contract_parquet as dlt_parquet
from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract_parquet as pandera_parquet

from phlo.operations.parquet_validation import evaluate_parquet_contract


class PositiveSchema(DataFrameModel):
    id: Series[int] = Field(gt=0)


@pytest.mark.parametrize("batch_size", [1, 7, 64])
def test_full_retains_exact_counts_and_caps_failure_sample(tmp_path, batch_size):
    paths = [tmp_path / "z.parquet", tmp_path / "a.parquet"]
    pd.DataFrame({"id": range(-13, 0)}).to_parquet(paths[0])
    pd.DataFrame({"id": range(-17, 3)}).to_parquet(paths[1])
    sizes = []
    errors = []

    def evaluate(frame):
        sizes.append(len(frame))
        result = evaluate_pandera_contract(frame, schema_class=PositiveSchema)
        errors.append(result.error)
        return result

    passed, failed, total, sample, error = evaluate_parquet_contract(
        paths, schema_class=PositiveSchema, evaluate=evaluate, mode="full", batch_size=batch_size
    )

    assert not passed
    assert (failed, total) == (31, 33)
    assert max(sizes) <= batch_size
    assert [case["failure_case"] for case in sample] == [*range(-13, 0), *range(-17, -10)]
    assert [case["index"] for case in sample] == list(range(20))
    assert error == errors[0] and error is not None


@pytest.mark.parametrize("sample_size", [2, 5, 20])
def test_sample_is_one_prefix_in_path_order(tmp_path, sample_size):
    paths = [tmp_path / "z.parquet", tmp_path / "a.parquet"]
    pd.DataFrame({"id": [8, -3, 2]}).to_parquet(paths[0])
    pd.DataFrame({"id": [-9, 6, -4, 7]}).to_parquet(paths[1])
    observed = []

    def evaluate(frame):
        observed.append(frame["id"].tolist())
        return evaluate_pandera_contract(frame, schema_class=PositiveSchema)

    result = evaluate_parquet_contract(
        paths,
        schema_class=PositiveSchema,
        evaluate=evaluate,
        mode="sample",
        batch_size=2,
        sample_size=sample_size,
    )
    assert observed == [[8, -3, 2, -9, 6, -4, 7][:sample_size]]
    assert result[2] == min(sample_size, 7)
    assert result[1] == {2: 1, 5: 2, 20: 3}[sample_size]


@pytest.mark.parametrize("parquet", [dlt_parquet, pandera_parquet])
def test_providers_forward_bounded_modes_and_sample_size(tmp_path, parquet):
    path = tmp_path / "late-failure.parquet"
    pd.DataFrame({"id": [1, 2, 3, -4]}).to_parquet(path)
    full = parquet(path, schema_class=PositiveSchema, mode="full", batch_size=2)
    sample = parquet(path, schema_class=PositiveSchema, mode="sample", batch_size=2, sample_size=3)
    assert not full.passed and (full.failed_count, full.total_count) == (1, 4)
    assert sample.passed and (sample.failed_count, sample.total_count) == (0, 3)


@pytest.mark.parametrize("parquet", [dlt_parquet, pandera_parquet])
def test_providers_reject_dataset_checks_before_reading(tmp_path, parquet):
    class DatasetSchema(DataFrameModel):
        id: Series[int]

        @dataframe_check
        def positive_sum(cls, frame):
            return frame["id"].sum() > 0

    with pytest.raises(ValueError, match="row-local contract"):
        parquet(tmp_path / "missing.parquet", schema_class=DatasetSchema, mode="full")


@pytest.mark.parametrize("parquet", [dlt_parquet, pandera_parquet])
def test_single_materialized_preserves_stored_index_and_ignores_sizes(tmp_path, parquet):
    class IndexedSchema(DataFrameModel):
        id: Series[int]
        index: Index[int] = Field(gt=10)

    path = tmp_path / "indexed.parquet"
    pd.DataFrame({"id": [1, 2]}, index=[19, 23]).to_parquet(path)
    result = parquet(path, schema_class=IndexedSchema, batch_size=0, sample_size=0)
    assert result.passed and result.total_count == 2
    combined = evaluate_pandera_contract_parquet_files([path], schema_class=IndexedSchema)
    assert not combined.passed and combined.failed_count == 2


@pytest.mark.parametrize("parquet", [dlt_parquet, pandera_parquet])
@pytest.mark.parametrize("mode", ["materialized", "full", "sample"])
def test_empty_file_still_validates_its_schema(tmp_path, parquet, mode):
    path = tmp_path / "empty.parquet"
    pd.DataFrame({"other": pd.Series(dtype="int64")}).to_parquet(path)
    result = parquet(path, schema_class=PositiveSchema, mode=mode)
    assert not result.passed
    assert result.total_count == 0 and result.failed_count == 1


@pytest.mark.parametrize(
    "options, message",
    [
        ({"mode": "unknown"}, "Unknown validation mode"),
        ({"mode": "full", "batch_size": 0}, "must be positive"),
        ({"mode": "sample", "sample_size": -1}, "must be positive"),
    ],
)
def test_invalid_options_fail_before_reading(options, message):
    with pytest.raises(ValueError, match=message):
        evaluate_parquet_contract(
            Path("missing.parquet"),
            schema_class=PositiveSchema,
            evaluate=lambda frame: evaluate_pandera_contract(frame, schema_class=PositiveSchema),
            **options,
        )
