"""Pandera contracts for the monitoring pack's raw tables.

Every ingestion asset validates against one of these before anything lands.
The contracts are the evidence stage's guarantee: a reading with an unknown
station or a claim with no line number cannot reach the warehouse.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series

from source_to_report.domain import POLLUTANT_IDS, POLLUTANTS_BY_ID, STATION_IDS


class ReadingSchema(pa.DataFrameModel):
    """One hourly measurement from one station."""

    reading_id: Series[str] = pa.Field(unique=True, str_matches=r"^[a-z]+-(pm25|no2)-\d{10}$")
    station_id: Series[str] = pa.Field(isin=list(STATION_IDS))
    station_name: Series[str] = pa.Field(nullable=False)
    pollutant: Series[str] = pa.Field(isin=list(POLLUTANT_IDS))
    observed_at: Series[str] = pa.Field(str_matches=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    observed_date: Series[str] = pa.Field(str_matches=r"^\d{4}-\d{2}-\d{2}$")
    value: Series[float] = pa.Field(ge=0.0, le=1000.0)
    unit: Series[str] = pa.Field(nullable=False)
    source: Series[str] = pa.Field(nullable=False)


class NoteSchema(pa.DataFrameModel):
    """One published monthly note, kept whole as evidence."""

    path: Series[str] = pa.Field(unique=True)
    month: Series[str] = pa.Field(str_matches=r"^\d{4}-\d{2}$")
    title: Series[str] = pa.Field(nullable=False)
    body: Series[str] = pa.Field(nullable=False)
    sha256: Series[str] = pa.Field(str_matches=r"^[0-9a-f]{64}$")


class ClaimSchema(pa.DataFrameModel):
    """One figure a note publishes, with the line it came from."""

    claim_id: Series[str] = pa.Field(unique=True)
    source_path: Series[str] = pa.Field(nullable=False)
    line: Series[int] = pa.Field(ge=1)
    month: Series[str] = pa.Field(str_matches=r"^\d{4}-\d{2}$")
    station_id: Series[str] = pa.Field(isin=list(STATION_IDS))
    pollutant: Series[str] = pa.Field(isin=list(POLLUTANT_IDS))
    metric: Series[str] = pa.Field(isin=["monthly_mean"])
    value: Series[float] = pa.Field(ge=0.0)
    unit: Series[str] = pa.Field(nullable=False)
    sentence: Series[str] = pa.Field(nullable=False)

    @pa.dataframe_check
    def unit_matches_pollutant(cls, data: pa.DataFrame) -> Series[bool]:
        """Every claim uses the unit the pollutant is measured in."""
        expected = data["pollutant"].map(lambda value: POLLUTANTS_BY_ID[value].unit)
        return data["unit"] == expected
