"""Contracts for telemetry readings, late corrections, and registry snapshots.

The raw reading contract intentionally allows duplicate ``message_id`` values:
hourly delivery files contain verbatim gateway retransmissions, and the raw
table uses an append strategy. Deduplication happens in the normalize stage.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series

TEMPERATURE_MIN_C = -40.0
TEMPERATURE_MAX_C = 85.0


class TelemetryReadingSchema(pa.DataFrameModel):
    """Physical-bound contract applied to every ingested telemetry batch.

    DLT normalizes ISO-8601 source strings to timestamps during staging, so
    the temporal columns are typed natively. ``event_hour`` carries hour
    truncations and becomes the hourly identity partition of the Iceberg
    table.
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
