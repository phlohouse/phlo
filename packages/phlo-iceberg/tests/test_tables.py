"""Tests for phlo-iceberg table helpers."""


def test_align_backfills_missing_provenance_columns() -> None:
    """Derived frames omit system bookkeeping columns; aligner fills them.

    Provenance columns (_dlt_* / _phlo_*) are written by the ingestion
    pipeline, not by derived transforms. The aligner must backfill them as
    nulls even though the table schema marks them required, instead of
    rejecting legitimate derived-frame writes.
    """
    from datetime import timezone

    import pyarrow as pa

    from phlo_iceberg.tables import _align_arrow_table_to_target_schema

    target = pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("_dlt_load_id", pa.string(), nullable=False),
            pa.field("_phlo_row_id", pa.string(), nullable=False),
            pa.field("_phlo_ingested_at", pa.timestamp("us", tz=timezone.utc), nullable=False),
        ]
    )
    data = pa.table({"id": pa.array(["a"])})

    aligned = _align_arrow_table_to_target_schema(data, target, table_name="demo")

    assert aligned.column_names == ["id", "_dlt_load_id", "_phlo_row_id", "_phlo_ingested_at"]
