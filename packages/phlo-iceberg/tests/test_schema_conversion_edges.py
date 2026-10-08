"""Schema conversion edge cases, including reserved fields and error boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock

import phlo_iceberg.schema_conversion as conversion
import pytest
from pandera.pandas import DataFrameModel


class Model(DataFrameModel):
    """A valid model whose annotation and column boundaries can be controlled."""

    value: str


def test_declared_metadata_keeps_reserved_ids_without_consuming_user_ids(monkeypatch) -> None:
    names = [
        "_dlt_load_id",
        "_dlt_id",
        "_phlo_row_id",
        "_phlo_ingested_at",
        "_phlo_partition_date",
        "_phlo_run_id",
    ]
    monkeypatch.setattr(
        conversion,
        "get_type_hints",
        lambda _: {
            "__ignored": int,
            "Config": int,
            "first": str,
            **dict.fromkeys(names, str),
            "last": int,
        },
    )
    monkeypatch.setattr(Model, "to_schema", lambda: SimpleNamespace(columns={}))
    schema = conversion.pandera_to_iceberg(Model, start_field_id=7)
    assert [field.name for field in schema.fields] == ["first", *names, "last"]
    assert [field.field_id for field in schema.fields] == [7, 100, 101, 103, 102, 104, 105, 8]
    assert all(not field.required and field.doc == "" for field in schema.fields)


def test_injected_metadata_preserves_order_types_and_docs() -> None:
    schema = conversion.pandera_to_iceberg(Model)
    assert [
        (field.name, field.field_id, str(field.field_type), field.required, field.doc)
        for field in schema.fields[1:]
    ] == [
        ("_dlt_load_id", 100, "string", True, "DLT load identifier"),
        ("_dlt_id", 101, "string", True, "DLT record identifier"),
        ("_phlo_row_id", 103, "string", True, "Phlo row-level lineage identifier (ULID)"),
        (
            "_phlo_ingested_at",
            102,
            "timestamptz",
            True,
            "UTC timestamp when phlo processed this record",
        ),
        (
            "_phlo_partition_date",
            104,
            "string",
            True,
            "Partition date used for ingestion (YYYY-MM-DD)",
        ),
        ("_phlo_run_id", 105, "string", True, "Dagster run ID for traceability"),
    ]


@pytest.mark.parametrize(
    ("boundary", "message"),
    [
        ("get_type_hints", "Failed to get type hints"),
        ("to_schema", "Failed to instantiate Pandera schema"),
    ],
)
def test_conversion_wraps_boundary_errors(monkeypatch, boundary: str, message: str) -> None:
    error = RuntimeError("broken boundary")
    if boundary == "get_type_hints":
        monkeypatch.setattr(conversion, boundary, Mock(side_effect=error))
    else:
        monkeypatch.setattr(Model, boundary, Mock(side_effect=error))
    with pytest.raises(conversion.SchemaConversionError, match=message) as caught:
        conversion.pandera_to_iceberg(Model)
    assert caught.value.__cause__ is error


def test_no_annotations_precedes_schema_instantiation(monkeypatch) -> None:
    monkeypatch.setattr(conversion, "get_type_hints", lambda _: {})
    build = Mock(side_effect=AssertionError("must not build"))
    monkeypatch.setattr(Model, "to_schema", build)
    with pytest.raises(conversion.SchemaConversionError, match="has no field annotations"):
        conversion.pandera_to_iceberg(Model)
    build.assert_not_called()
