"""Contracts for civic, weather, and demographic source tables.

Temporal fields are typed natively because DLT normalizes ISO-8601 strings to
timestamps during staging. ``ObservationSchema`` carries the schema-drift
surface: ``pressure_hpa`` is optional because archives before July 2026 do
not report it; the drift batch must validate while malformed values (negative
precipitation) fail closed at ingest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandera.pandas as pa
from pandera.typing import Series

TEMP_MIN_C = -90.0
TEMP_MAX_C = 140.0  # Fahrenheit-flagged rows store raw Fahrenheit until staging


class PlaceRecordSchema(pa.DataFrameModel):
    """One civic registry row; ``place_id`` merges revisions in place."""

    place_id: Series[str] = pa.Field(str_matches=r"^P[0-9]{1,3}$")
    name: Series[str]
    region: Series[str] = pa.Field(isin=["north", "south"])
    lat: Series[float] = pa.Field(ge=-90.0, le=90.0)
    lon: Series[float] = pa.Field(ge=-180.0, le=180.0)
    population_year: Series[int] = pa.Field(ge=1900, le=2100)
    population: Series[int] = pa.Field(ge=0)
    registry_date: Series[datetime]

    class Config:
        strict = False
        coerce = True


class PlacesGeoSchema(pa.DataFrameModel):
    """Flattened GeoJSON metadata; centroid derives from the polygon ring."""

    place_id: Series[str] = pa.Field(unique=True)
    centroid_lat: Series[float] = pa.Field(ge=-90.0, le=90.0)
    centroid_lon: Series[float] = pa.Field(ge=-180.0, le=180.0)
    prop_region_code: Series[str]
    prop_elevation_m: Series[float] = pa.Field(ge=0.0)
    prop_classification: Series[str] = pa.Field(isin=["city", "town", "village"])

    class Config:
        strict = False
        coerce = True


class ObservationSchema(pa.DataFrameModel):
    """One station observation appended by its natural key.

    ``temp_c`` holds the raw archive value: Fahrenheit-flagged rows store the
    unconverted value with ``unit_f = true`` and are normalized to Celsius in
    the staging model. ``pressure_hpa`` is optional - only the drift month
    reports it.
    """

    observation_key: Series[str] = pa.Field(unique=True)

    station_id: Series[str]
    obs_month: Series[datetime]
    observed_at: Series[datetime]
    temp_c: Series[float] = pa.Field(ge=TEMP_MIN_C, le=TEMP_MAX_C)
    precip_mm: Series[float] = pa.Field(ge=0.0)
    unit_f: Series[bool]
    pressure_hpa: Optional[Series[float]] = pa.Field(nullable=True, ge=800.0, le=1200.0)

    class Config:
        strict = False
        coerce = True


class RegionDemographicsSchema(pa.DataFrameModel):
    """Annual regional statistics merged on the ``(region, year)`` surrogate."""

    region_year: Series[str] = pa.Field(unique=True)
    region: Series[str] = pa.Field(isin=["north", "south"])
    year: Series[int] = pa.Field(ge=1900, le=2100)
    population: Series[int] = pa.Field(ge=0)
    median_age: Series[float] = pa.Field(ge=0.0, le=100.0)
    census_year: Series[datetime]

    class Config:
        strict = False
        coerce = True
