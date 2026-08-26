"""Tests for Pandera-to-ClickHouse schema conversion.

Pins the arrow type mappings, metadata-column injection, and Optional
unwrapping that the table store relies on for DDL rendering and parquet
coercion.
"""

from datetime import datetime
from decimal import Decimal
import pyarrow as pa
import pytest
from pandera.pandas import DataFrameModel

from phlo_clickhouse.schema_conversion import (
    SchemaConversionError,
    pandera_to_arrow,
)


class OrderSchema(DataFrameModel):
    order_id: str
    quantity: int
    price: float
    is_gift: bool
    ordered_at: datetime


def test_pandera_to_arrow_maps_scalar_types() -> None:
    """Scalar annotations map onto the expected arrow types in order."""
    schema = pandera_to_arrow(OrderSchema, add_dlt_metadata=False, add_phlo_metadata=False)

    assert schema.field("order_id").type == pa.string()
    assert schema.field("quantity").type == pa.int64()
    assert schema.field("price").type == pa.float64()
    assert schema.field("is_gift").type == pa.bool_()
    assert schema.field("ordered_at").type == pa.timestamp("us", tz="UTC")
    assert schema.field("ordered_at").nullable is False


def test_pandera_to_arrow_appends_metadata_columns() -> None:
    """DLT and Phlo traceability columns are appended after user columns."""
    schema = pandera_to_arrow(OrderSchema)
    names = schema.names[-6:]
    assert names == [
        "_dlt_load_id",
        "_dlt_id",
        "_phlo_ingested_at",
        "_phlo_row_id",
        "_phlo_partition_date",
        "_phlo_run_id",
    ]
    assert schema.field("_phlo_ingested_at").type == pa.timestamp("us", tz="UTC")


def test_pandera_to_arrow_metadata_flags_disable_injection() -> None:
    """Disabling metadata flags leaves only user columns."""
    schema = pandera_to_arrow(
        OrderSchema,
        add_dlt_metadata=False,
        add_phlo_metadata=True,
    )
    assert "_dlt_load_id" not in schema.names
    assert "_phlo_row_id" in schema.names


def test_pandera_to_arrow_unwraps_optional() -> None:
    """Optional scalars map to their non-null arrow counterpart."""

    class DiscountSchema(DataFrameModel):
        code: str | None
        applied_at: datetime | None

    schema = pandera_to_arrow(DiscountSchema, add_dlt_metadata=False, add_phlo_metadata=False)
    assert schema.field("code").type == pa.string()
    assert schema.field("applied_at").type == pa.timestamp("us", tz="UTC")


def test_pandera_to_arrow_rejects_lists() -> None:
    """List columns cannot be represented in a typed ClickHouse table."""

    class TagSchema(DataFrameModel):
        tags: list[str]

    with pytest.raises(SchemaConversionError):
        pandera_to_arrow(TagSchema, add_dlt_metadata=False, add_phlo_metadata=False)


def test_pandera_to_arrow_decimal_maps_to_float() -> None:
    """Decimal mirrors the Iceberg converter's Double mapping."""

    class MoneySchema(DataFrameModel):
        amount: Decimal

    schema = pandera_to_arrow(MoneySchema, add_dlt_metadata=False, add_phlo_metadata=False)
    assert schema.field("amount").type == pa.float64()
