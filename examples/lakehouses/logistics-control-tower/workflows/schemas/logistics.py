"""Contracts for replicated orders, carrier events, warehouse scans, and references.

DLT normalizes ISO-8601 source strings to timestamps during staging, so every
temporal field is typed natively as ``Series[datetime]`` rather than strings.
"""

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series


class ShipmentOrderSchema(pa.DataFrameModel):
    """Replicated order state from the logistics PostgreSQL source."""

    order_id: Series[str] = pa.Field(str_matches=r"^ORD-\d{4}$")
    customer_ref: Series[str] = pa.Field(str_matches=r"^CUST-\d{3}$")
    status: Series[str] = pa.Field(
        isin=["pending", "allocated", "shipped", "delivered", "cancelled"]
    )
    ordered_at: Series[datetime]
    updated_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


class CarrierEventSchema(pa.DataFrameModel):
    """Carrier scan event served by the replay API."""

    event_id: Series[str] = pa.Field(unique=True)
    carrier: Series[str] = pa.Field(str_matches=r"^[A-Z]{4,10}$")
    shipment_id: Series[str] = pa.Field(str_matches=r"^SHP-\d{4}$")
    event_type: Series[str] = pa.Field(isin=["pickup", "in_transit", "delivered", "exception"])
    event_time: Series[datetime]
    location: Series[str]

    class Config:
        strict = False
        coerce = True


class WarehouseScanSchema(pa.DataFrameModel):
    """Physical inbound/outbound scans captured by warehouse handlers."""

    scan_id: Series[str] = pa.Field(unique=True)
    warehouse_id: Series[str] = pa.Field(str_matches=r"^WH-[A-Z]+-\d{2}$")
    shipment_id: Series[str] = pa.Field(str_matches=r"^SHP-\d{4}$")
    scan_type: Series[str] = pa.Field(isin=["inbound", "outbound"])
    scanned_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


class CarrierDirectorySchema(pa.DataFrameModel):
    """Registered carriers; the ingestion gate validates against these codes."""

    carrier_code: Series[str] = pa.Field(unique=True, str_matches=r"^[A-Z]{4,10}$")
    carrier_name: Series[str]
    dispatch_email: Series[str] = pa.Field(str_matches=r"^[^@]+@[^@]+$")
    polling_minutes: Series[int] = pa.Field(ge=15)

    class Config:
        strict = False
        coerce = True


class SlaTermSchema(pa.DataFrameModel):
    """Contractual transit allowance per carrier and service level.

    ``sla_hours`` carries no positivity bound here on purpose: a negative SLA
    clock is an operational invariant owned by the SLA validator, not a physical
    schema bound, so the labeled failure fixture breaks exactly one rule.
    """

    carrier_code: Series[str]
    service_level: Series[str] = pa.Field(isin=["standard", "express"])
    sla_hours: Series[float]
    sla_term_key: Series[str] = pa.Field(unique=True)

    class Config:
        strict = False
        coerce = True


class OrderCurrentStateSchema(pa.DataFrameModel):
    """Read-time collapse of replicated order versions to one current row."""

    order_id: Series[str] = pa.Field(unique=True)
    customer_ref: Series[str]
    current_status: Series[str]
    ordered_at: Series[datetime]
    last_updated_at: Series[datetime]

    class Config:
        strict = False
        coerce = True
