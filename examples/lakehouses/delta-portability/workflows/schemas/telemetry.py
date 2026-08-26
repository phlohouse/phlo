"""Contracts for telemetry readings, late corrections, and registry snapshots.

The raw reading contract intentionally allows duplicate ``message_id`` values:
hourly delivery files contain verbatim gateway retransmissions, and the raw
table uses an append strategy. Deduplication happens in the normalize stage.

``signal_quality_dbm`` is the additive schema-evolution column: firmware v2
emits it from hour T06 onward while v1 batches never do. It is optional with
a physical bound so one contract covers both firmware generations.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series

TEMPERATURE_MIN_C = -40.0
TEMPERATURE_MAX_C = 85.0
SIGNAL_QUALITY_MIN_DBM = -120.0
SIGNAL_QUALITY_MAX_DBM = -40.0


class TelemetryReadingSchema(pa.DataFrameModel):
    """Physical-bound contract applied to every ingested telemetry batch.

    DLT normalizes ISO-8601 source strings to timestamps during staging, so
    the temporal columns are typed natively. ``event_date`` carries the day
    truncation of ``event_hour`` and becomes the identity partition of the
    Delta table: Delta supports identity-only partition transforms, unlike
    the Iceberg hourly transform used by the sibling example.
    """

    message_id: Series[str]
    device_id: Series[str] = pa.Field(str_matches=r"^dev-\d{3}$")
    site_id: Series[str] = pa.Field(str_matches=r"^site-[a-z]+$")
    sequence_number: Series[int] = pa.Field(ge=1)
    event_time: Series[datetime]
    event_hour: Series[datetime]
    ingested_from_hour: Series[datetime]
    temperature_c: Series[float] = pa.Field(ge=TEMPERATURE_MIN_C, le=TEMPERATURE_MAX_C)
    humidity_pct: Series[float] = pa.Field(ge=0.0, le=100.0)
    battery_pct: Series[float] = pa.Field(ge=0.0, le=100.0)
    firmware: Series[str]
    rssi_dbm: Series[int] = pa.Field(ge=-120, le=-1)
    signal_quality_dbm: Series[float] | None = pa.Field(
        nullable=True, ge=SIGNAL_QUALITY_MIN_DBM, le=SIGNAL_QUALITY_MAX_DBM
    )
    event_date: Series[str] = pa.Field(str_matches=r"^\d{4}-\d{2}-\d{2}$")

    class Config:
        strict = False
        coerce = True


class TelemetryCorrectionSchema(pa.DataFrameModel):
    """Late corrections amend an existing reading identified by message id."""

    message_id: Series[str]
    corrected_temperature_c: Series[float] | None = pa.Field(
        nullable=True, ge=TEMPERATURE_MIN_C, le=TEMPERATURE_MAX_C
    )
    corrected_humidity_pct: Series[float] | None = pa.Field(nullable=True, ge=0.0, le=100.0)
    correction_reason: Series[str]
    corrected_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


class DeviceRegistrySchema(pa.DataFrameModel):
    """Current fleet registry snapshot merged from the device database."""

    device_id: Series[str] = pa.Field(unique=True)
    site_id: Series[str]
    model: Series[str]
    activated_at: Series[str]
    decommissioned_at: Series[str] | None = pa.Field(nullable=True)

    class Config:
        strict = False
        coerce = True


class SiteDirectorySchema(pa.DataFrameModel):
    """Site reference data merged from the device database."""

    site_id: Series[str] = pa.Field(unique=True)
    site_name: Series[str]
    region: Series[str]

    class Config:
        strict = False
        coerce = True


class RegionDirectorySchema(pa.DataFrameModel):
    """Regions lookup replicated from PostgreSQL through Sling."""

    region_code: Series[str] = pa.Field(unique=True)
    region_name: Series[str]
    country: Series[str]
    updated_at: Series[datetime]

    class Config:
        strict = False
        coerce = True
