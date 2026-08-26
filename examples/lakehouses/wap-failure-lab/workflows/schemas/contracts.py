"""Contracts for sensor batches ingested by the WAP failure lab.

One core dataset shape feeds every scenario: ``sensor_batches``. The contract
is deliberately strict (non-null readings, bounded values, known quality
flags) so each labeled failure fixture breaks exactly one named invariant.
``reading_quality_score`` is the additive schema-change field: optional and
nullable, so pre-change batches validate unchanged against the same model.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series

READING_VALUE_MIN = -50.0
READING_VALUE_MAX = 150.0
QUALITY_SCORE_MIN = 0.0
QUALITY_SCORE_MAX = 100.0

BASE_COLUMNS = (
    "batch_id",
    "sensor_id",
    "reading_value",
    "recorded_at",
    "batch_date",
    "quality_flag",
)


class SensorBatchSchema(pa.DataFrameModel):
    """Physical-bound contract applied to every ingested sensor batch.

    DLT normalizes ISO-8601 source strings to timestamps during staging, so
    the temporal columns are typed natively. ``batch_date`` becomes the daily
    identity partition of the Iceberg table.
    """

    batch_id: Series[str] = pa.Field(str_matches=r"^b-\d{4}$")
    sensor_id: Series[str] = pa.Field(str_matches=r"^s-\d{3}$")
    reading_value: Series[float] = pa.Field(
        nullable=False, ge=READING_VALUE_MIN, le=READING_VALUE_MAX
    )
    recorded_at: Series[datetime]
    batch_date: Series[datetime]
    quality_flag: Series[str] = pa.Field(isin=["ok", "suspect"])
    reading_quality_score: Series[float] | None = pa.Field(
        nullable=True,
        ge=QUALITY_SCORE_MIN,
        le=QUALITY_SCORE_MAX,
    )

    class Config:
        strict = False
        coerce = True
