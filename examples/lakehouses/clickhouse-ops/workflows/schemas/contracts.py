"""Contracts for platform events, access logs, and tenant metadata.

Raw ingestion is append-only: the delivery layer replays quarter-hour
micro-batches verbatim, so duplicate ``event_id`` rows are expected in raw
and allowed by these contracts. Deduplication happens at read time in the
operational marts, mirroring how the ClickHouse data plane has no snapshot
isolation to hide replays behind.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series

LATENCY_MIN_MS = 0
LATENCY_MAX_MS = 60000
ALLOWED_STATUS_CODES = (200, 201, 204, 400, 404, 500, 502, 503)
TIER1_TENANTS = ("t-northwind", "t-acme")


class PlatformEventSchema(pa.DataFrameModel):
    """Bounds contract for every ingested platform-event micro-batch.

    DLT normalizes ISO-8601 source strings to timestamps during staging, so
    temporal columns are typed natively. ``occurred_hour`` carries the hour
    truncation and becomes the hourly identity partition.
    """

    event_id: Series[str]
    tenant_id: Series[str] = pa.Field(str_matches=r"^t-[a-z]+$")
    event_type: Series[str]
    occurred_at: Series[datetime]
    occurred_hour: Series[datetime]
    latency_ms: Series[int] = pa.Field(ge=LATENCY_MIN_MS, le=LATENCY_MAX_MS)

    class Config:
        strict = False
        coerce = True


class AccessLogSchema(pa.DataFrameModel):
    """Status-catalog and duration contract for every request-log batch."""

    request_id: Series[str]
    tenant_id: Series[str] = pa.Field(str_matches=r"^t-[a-z]+$")
    path: Series[str] = pa.Field(str_matches=r"^/api/")
    status_code: Series[int] = pa.Field(isin=list(ALLOWED_STATUS_CODES))
    duration_ms: Series[int] = pa.Field(ge=0, le=60000)
    occurred_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


class TenantSchema(pa.DataFrameModel):
    """Tenant directory replicated nightly from PostgreSQL metadata."""

    tenant_id: Series[str] = pa.Field(unique=True)
    tenant_name: Series[str]
    tier: Series[str] = pa.Field(isin=["tier-1", "tier-2"])
    plan: Series[str]

    class Config:
        strict = False
        coerce = True
