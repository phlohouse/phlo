"""Tests for phlo-iceberg table helpers."""


def test_align_backfills_optional_provenance_columns() -> None:
    """Derived writes can omit provenance only when the schema makes it optional."""
    from datetime import timezone

    import pyarrow as pa

    from phlo_iceberg.schema_alignment import _align_arrow_table_to_target_schema

    target = pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("_dlt_load_id", pa.string(), nullable=True),
            pa.field("_phlo_row_id", pa.string(), nullable=True),
            pa.field("_phlo_ingested_at", pa.timestamp("us", tz=timezone.utc), nullable=True),
        ]
    )
    data = pa.table({"id": pa.array(["a"])})

    aligned = _align_arrow_table_to_target_schema(data, target, table_name="demo")

    assert aligned.column_names == ["id", "_dlt_load_id", "_phlo_row_id", "_phlo_ingested_at"]
    assert aligned.to_pylist() == [
        {"id": "a", "_dlt_load_id": None, "_phlo_row_id": None, "_phlo_ingested_at": None}
    ]
