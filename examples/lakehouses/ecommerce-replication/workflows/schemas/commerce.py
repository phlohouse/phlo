"""Pandera contracts for the replicated commerce source.

These models describe the shape the source database guarantees. They are used
by the domain checks in ``workflows.domains`` and by pytest to validate both
fixture output and diagnostic reads of replicated tables.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series


class CustomerSchema(pa.DataFrameModel):
    customer_id: Series[str] = pa.Field(unique=True)
    email: Series[str] = pa.Field(str_contains="@")
    full_name: Series[str]
    segment: Series[str] = pa.Field(isin=["consumer", "business", "enterprise"])
    region: Series[str] = pa.Field(isin=["north", "south", "east", "west"])
    signup_date: Series[str]
    updated_at: Series[str]

    class Config:
        # Replicated tables carry Phlo `_phlo_*` lineage columns alongside
        # source columns (and snapshot metadata for snapshot-mode streams).
        strict = False
        coerce = True


class ProductSchema(pa.DataFrameModel):
    product_id: Series[str] = pa.Field(unique=True)
    sku: Series[str] = pa.Field(unique=True)
    name: Series[str]
    category: Series[str]
    unit_price: Series[float] = pa.Field(gt=0)
    active: Series[bool]
    created_at: Series[str]
    updated_at: Series[str]

    class Config:
        strict = False
        coerce = True


class OrderSchema(pa.DataFrameModel):
    order_id: Series[str] = pa.Field(unique=True)
    customer_id: Series[str]
    status: Series[str] = pa.Field(isin=["pending", "shipped", "delivered", "cancelled"])
    currency: Series[str] = pa.Field(isin=["USD"])
    total_amount: Series[float] = pa.Field(ge=0)
    ordered_at: Series[str]
    updated_at: Series[str]

    class Config:
        strict = False
        coerce = True


class OrderLineSchema(pa.DataFrameModel):
    # Composite key: (order_id, line_id) must be unique together.
    order_id: Series[str]
    line_id: Series[str]
    product_id: Series[str]
    quantity: Series[int] = pa.Field(gt=0)
    unit_price: Series[float] = pa.Field(gt=0)
    line_amount: Series[float] = pa.Field(ge=0)
    updated_at: Series[str]

    class Config:
        strict = False
        coerce = True


class PaymentSchema(pa.DataFrameModel):
    payment_id: Series[str] = pa.Field(unique=True)
    order_id: Series[str]
    method: Series[str] = pa.Field(isin=["card", "paypal", "bank"])
    amount: Series[float] = pa.Field(gt=0)
    paid_at: Series[str]
    updated_at: Series[str]

    class Config:
        strict = False
        coerce = True
