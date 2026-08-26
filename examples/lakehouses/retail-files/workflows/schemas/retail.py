"""Pandera contracts for staged retail file ingestion."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series


class SalesSchema(pa.DataFrameModel):
    transaction_id: Series[str]
    line_id: Series[str] = pa.Field(unique=True)
    store_id: Series[str]
    register_id: Series[str]
    cashier_id: Series[str]
    customer_id: Series[str]
    product_id: Series[str]
    sold_at: Series[str]
    quantity: Series[int]
    list_price: Series[float] = pa.Field(gt=0)
    unit_price: Series[float] = pa.Field(gt=0)
    gross_amount: Series[float]
    discount_amount: Series[float]
    tax_amount: Series[float]
    net_amount: Series[float]
    currency: Series[str] = pa.Field(isin=["USD"])
    payment_method: Series[str] = pa.Field(isin=["card", "cash", "wallet"])
    channel: Series[str] = pa.Field(isin=["store", "online"])
    promotion_id: Series[str] | None = pa.Field(nullable=True)
    is_return: Series[bool]
    partition_date: Series[str]

    class Config:
        # Phlo validates staged parquet after adding its four `_phlo_*` lineage columns.
        strict = False
        coerce = True


class ProductsSchema(pa.DataFrameModel):
    product_id: Series[str] = pa.Field(unique=True)
    product_name: Series[str]
    category: Series[str]
    subcategory: Series[str]
    brand: Series[str]
    supplier_id: Series[str]
    unit_cost: Series[float] = pa.Field(ge=0)
    list_price: Series[float] = pa.Field(gt=0)
    active: Series[bool]
    created_at: Series[str]
    updated_at: Series[str]

    class Config:
        strict = False
        coerce = True


class StoresSchema(pa.DataFrameModel):
    store_id: Series[str] = pa.Field(unique=True)
    store_name: Series[str]
    region: Series[str] = pa.Field(isin=["north", "south", "east", "west"])
    format: Series[str] = pa.Field(isin=["urban", "mall", "outlet"])
    timezone: Series[str]
    open_date: Series[str]

    class Config:
        strict = False
        coerce = True


class PromotionsSchema(pa.DataFrameModel):
    promotion_id: Series[str] = pa.Field(unique=True)
    promotion_name: Series[str]
    discount_rate: Series[float] = pa.Field(gt=0, le=1)

    class Config:
        strict = False
        coerce = True


class InventorySchema(pa.DataFrameModel):
    inventory_snapshot_id: Series[str] = pa.Field(unique=True)
    store_id: Series[str]
    product_id: Series[str]
    observed_at: Series[str]
    on_hand: Series[int] = pa.Field(ge=0)
    reserved: Series[int] = pa.Field(ge=0)
    in_transit: Series[int] = pa.Field(ge=0)
    reorder_point: Series[int] = pa.Field(ge=0)
    safety_stock: Series[int] = pa.Field(ge=0)
    partition_date: Series[str]

    class Config:
        strict = False
        coerce = True
