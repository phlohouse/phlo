"""Behaviour tests for Pandera-to-Delta schema conversion."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import pandera.pandas as pa_pandas
import pyarrow as pa
import pytest
from pandera.pandas import DataFrameModel
from pandera.typing import Series

from phlo_delta.schema_conversion import SchemaConversionError, pandera_to_delta

DLT_FIELDS = [
    pa.field("_dlt_load_id", pa.string(), nullable=False),
    pa.field("_dlt_id", pa.string(), nullable=False),
]
PHLO_FIELDS = [
    pa.field("_phlo_row_id", pa.string(), nullable=False),
    pa.field("_phlo_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("_phlo_partition_date", pa.string(), nullable=False),
    pa.field("_phlo_run_id", pa.string(), nullable=False),
]


def _model(annotation: Any) -> type[DataFrameModel]:
    return type("Model", (DataFrameModel,), {"__annotations__": {"value": annotation}})


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str, pa.string()),
        (int, pa.int64()),
        (float, pa.float64()),
        (bool, pa.bool_()),
        (datetime, pa.timestamp("us", tz="UTC")),
        (date, pa.date32()),
        (bytes, pa.binary()),
        (Decimal, pa.float64()),
        (Series[int], pa.int64()),
        (Optional[int], pa.int64()),
        (int | None, pa.int64()),
        (Optional[Decimal], pa.float64()),
    ],
)
def test_maps_each_supported_annotation_to_arrow_type(annotation: Any, expected: pa.DataType):
    schema = pandera_to_delta(_model(annotation), add_dlt_metadata=False, add_phlo_metadata=False)

    assert schema.field("value").type == expected


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (complex, "Unsupported type for field value"),
        (list[int], "Lists are not supported for field value"),
        (dict[str, int], "Dicts are not supported for field value"),
        (Optional[complex], "Unsupported type for field value"),
    ],
)
def test_rejects_unsupported_annotations(annotation: Any, message: str):
    with pytest.raises(SchemaConversionError, match="Cannot map Pandera type for field value") as e:
        pandera_to_delta(_model(annotation))

    assert message in str(e.value)


def test_carries_nullability_from_pandera_fields():
    class Events(DataFrameModel):
        event_id: str = pa_pandas.Field(nullable=False)
        note: str = pa_pandas.Field(nullable=True)

    schema = pandera_to_delta(Events, add_dlt_metadata=False, add_phlo_metadata=False)

    assert schema == pa.schema(
        [
            pa.field("event_id", pa.string(), nullable=False),
            pa.field("note", pa.string(), nullable=True),
        ]
    )


def test_appends_dlt_then_phlo_metadata_columns_by_default():
    class Events(DataFrameModel):
        event_id: str

    schema = pandera_to_delta(Events)

    assert list(schema) == [
        pa.field("event_id", pa.string(), nullable=False),
        *DLT_FIELDS,
        *PHLO_FIELDS,
    ]


@pytest.mark.parametrize(
    ("add_dlt", "add_phlo", "expected_metadata"),
    [
        (True, False, DLT_FIELDS),
        (False, True, PHLO_FIELDS),
        (False, False, []),
    ],
)
def test_metadata_flags_select_metadata_columns(add_dlt, add_phlo, expected_metadata):
    class Events(DataFrameModel):
        event_id: str

    schema = pandera_to_delta(Events, add_dlt_metadata=add_dlt, add_phlo_metadata=add_phlo)

    assert list(schema)[1:] == expected_metadata


def test_user_declared_metadata_columns_are_not_duplicated():
    class Events(DataFrameModel):
        _dlt_id: int
        _phlo_run_id: int

    Events.__annotations__ = {"_dlt_id": int, "_phlo_run_id": int}

    schema = pandera_to_delta(Events)

    assert schema.names.count("_dlt_id") == 1
    assert schema.names.count("_phlo_run_id") == 1
    assert schema.field("_dlt_id").type == pa.int64()
    assert schema.field("_phlo_run_id").type == pa.int64()
    assert set(schema.names) == {f.name for f in DLT_FIELDS + PHLO_FIELDS}


def test_rejects_schema_without_annotations():
    class Empty:
        pass

    with pytest.raises(SchemaConversionError, match="has no field annotations"):
        pandera_to_delta(Empty)


def test_rejects_schema_with_only_dunder_or_config_fields():
    model = type("Hidden", (DataFrameModel,), {"__annotations__": {"__private__": int}})

    with pytest.raises(SchemaConversionError, match="No fields found in Pandera schema Hidden"):
        pandera_to_delta(model)


def test_rejects_unresolvable_annotations():
    model = type("Broken", (DataFrameModel,), {"__annotations__": {"value": "MissingType"}})

    with pytest.raises(SchemaConversionError, match="Failed to get type hints"):
        pandera_to_delta(model)


def test_rejects_schema_that_pandera_cannot_build():
    class Broken(DataFrameModel):
        value: int

        @classmethod
        def to_schema(cls):
            raise ValueError("bad check")

    with pytest.raises(SchemaConversionError, match="Failed to instantiate Pandera schema Broken"):
        pandera_to_delta(Broken)
