# Workflow Development Guide

## Building Your First Data Pipeline in Phlo

This guide walks you through creating a complete data pipeline from scratch. We'll build a pipeline that ingests weather data from an API, transforms it through Bronze/Silver/Gold layers, and publishes it for analytics.

---

## Authoring Loop

Use this loop when creating a new ingestion workflow:

```bash
phlo workflow create --type ingestion --domain weather --table observations --unique-key station_id
phlo workflow check workflows/ingestion/weather/observations.py
phlo services restart --service dagster
phlo materialize dlt_observations
phlo status
```

`phlo workflow check` validates the workflow file and the inferred schema file before you
restart Dagster or materialize the asset.

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Prerequisites](#prerequisites)
3. [Step 1: Define Your Data Schema](#step-1-define-your-data-schema)
4. [Step 2: Create the Ingestion Asset](#step-2-create-the-ingestion-asset)
5. [Step 3: Create Bronze Layer (Staging)](#step-3-create-bronze-layer-staging)
6. [Step 4: Create Silver Layer (Facts)](#step-4-create-silver-layer-facts)
7. [Step 5: Create Gold Layer (Aggregations)](#step-5-create-gold-layer-aggregations)
8. [Step 6: Create Marts for BI](#step-6-create-marts-for-bi)
9. [Step 7: Add Data Quality Checks](#step-7-add-data-quality-checks)
10. [Step 8: Configure Publishing](#step-8-configure-publishing)
11. [Step 9: Add Scheduling](#step-9-add-scheduling)
12. [Step 10: Test and Deploy](#step-10-test-and-deploy)
13. [Advanced Patterns](#advanced-patterns)

---

## Pipeline Overview

### What We're Building

A weather data pipeline that:

1. Fetches weather data from OpenWeather API
2. Stores raw data in Iceberg (Bronze)
3. Cleans and standardizes data (Silver)
4. Calculates daily statistics (Gold)
5. Publishes to PostgreSQL for dashboards (Marts)
6. Runs automatically every hour

### Architecture

```mermaid
flowchart LR
    weather[OpenWeather API]
    asset["Dagster asset<br/>weather_data"]
    raw["Iceberg<br/>raw.weather_observations"]
    bronze["dbt<br/>stg_weather_observations"]
    bronze_table["Iceberg<br/>bronze.stg_weather_observations"]
    silver["dbt<br/>fct_weather_readings"]
    silver_table["Iceberg<br/>silver.fct_weather_readings"]
    gold["dbt<br/>mrt_daily_weather_summary"]
    marts_table["Iceberg<br/>marts.mrt_daily_weather_summary"]
    publish["Dagster asset<br/>publish_weather_marts"]
    postgres["PostgreSQL marts"]
    superset[Superset dashboard]

    weather --> asset --> raw --> bronze --> bronze_table --> silver --> silver_table --> gold --> marts_table --> publish --> postgres --> superset
```

---

## Prerequisites

Before starting, make sure you have:

1. ✅ Phlo running (`phlo services start --profile core --profile query`)
2. ✅ Basic understanding of SQL
3. ✅ OpenWeather API key (free at https://openweathermap.org/api)
4. ✅ Text editor or IDE

---

## Choosing a Template

| Template | Use when |
| --- | --- |
| `minimal` | You want an empty Phlo project. |
| `basic` | You want a dbt-ready project. |
| `csv-batch` | You want the smallest local pipeline with no network calls. |
| `api-ingestion` | You want a REST-style ingestion starting point. |
| `dbt-medallion` | You want bronze/silver/gold transform structure. |
| `sling-replication` | You want to start with Sling replication. |
| `observability-demo` | You want a tiny pipeline wired for telemetry. |

---

## Step 1: Define Your Data Schema

First, let's define what our data looks like.

### 1.1 Create Schema Definition

Create a new file: `workflows/schemas/weather.py`

```python
"""Schema definitions for weather data."""

import pandera as pa
from pandera.typing import Series
from datetime import datetime

class WeatherObservationSchema(pa.DataFrameModel):
    """Schema for raw weather observations from OpenWeather API."""

    # Identifiers
    city_name: Series[str] = pa.Field(description="City name")
    country: Series[str] = pa.Field(description="Country code")
    latitude: Series[float] = pa.Field(ge=-90, le=90, description="Latitude")
    longitude: Series[float] = pa.Field(ge=-180, le=180, description="Longitude")

    # Weather data
    temperature: Series[float] = pa.Field(description="Temperature in Celsius")
    feels_like: Series[float] = pa.Field(description="Feels like temperature")
    humidity: Series[int] = pa.Field(ge=0, le=100, description="Humidity percentage")
    pressure: Series[int] = pa.Field(description="Atmospheric pressure in hPa")
    wind_speed: Series[float] = pa.Field(ge=0, description="Wind speed in m/s")

    # Weather conditions
    weather_main: Series[str] = pa.Field(description="Main weather condition (Rain, Clear, etc.)")
    weather_description: Series[str] = pa.Field(description="Detailed description")

    # Timestamps
    observation_time: Series[datetime] = pa.Field(description="Time of observation")
    sunrise_time: Series[datetime] = pa.Field(description="Sunrise time")
    sunset_time: Series[datetime] = pa.Field(description="Sunset time")

    class Config:
        """Pandera configuration."""
        coerce = True  # Coerce types automatically
        strict = False  # Allow extra columns


class WeatherReadingSchema(pa.DataFrameModel):
    """Schema for transformed weather readings (silver layer)."""

    # All fields from raw, plus calculated fields
    city_name: Series[str]
    temperature: Series[float]
    humidity: Series[int]
    observation_time: Series[datetime]

    # Calculated fields
    temperature_f: Series[float] = pa.Field(description="Temperature in Fahrenheit")
    temp_category: Series[str] = pa.Field(
        isin=["Freezing", "Cold", "Mild", "Warm", "Hot"],
        description="Temperature category"
    )
    is_daytime: Series[bool] = pa.Field(description="True if during daylight")

    class Config:
        coerce = True
        strict = False
```

**What this does:**

- Defines the structure of our data
- Validates data types
- Adds constraints (e.g., humidity 0-100%)
- Documents each field
- Enables automatic testing

### 1.2 Create Iceberg Table Schema (Optional)

Create: `workflows/schemas/iceberg_weather.py`

```python
"""Iceberg table schemas for weather data."""

from pyiceberg.schema import Schema
from pyiceberg.types import (
    NestedField,
    StringType,
    FloatType,
    IntegerType,
    TimestampType,
    BooleanType,
)

WEATHER_OBSERVATION_SCHEMA = Schema(
    NestedField(1, "city_name", StringType(), required=True),
    NestedField(2, "country", StringType(), required=True),
    NestedField(3, "latitude", FloatType(), required=True),
    NestedField(4, "longitude", FloatType(), required=True),
    NestedField(5, "temperature", FloatType(), required=True),
    NestedField(6, "feels_like", FloatType(), required=True),
    NestedField(7, "humidity", IntegerType(), required=True),
    NestedField(8, "pressure", IntegerType(), required=True),
    NestedField(9, "wind_speed", FloatType(), required=True),
    NestedField(10, "weather_main", StringType(), required=True),
    NestedField(11, "weather_description", StringType(), required=True),
    NestedField(12, "observation_time", TimestampType(), required=True),
    NestedField(13, "sunrise_time", TimestampType(), required=True),
    NestedField(14, "sunset_time", TimestampType(), required=True),
)

WEATHER_READING_SCHEMA = Schema(
    # Raw fields
    NestedField(1, "city_name", StringType(), required=True),
    NestedField(2, "country", StringType(), required=True),
    NestedField(3, "temperature", FloatType(), required=True),
    NestedField(4, "humidity", IntegerType(), required=True),
    NestedField(5, "observation_time", TimestampType(), required=True),

    # Calculated fields
    NestedField(6, "temperature_f", FloatType(), required=False),
    NestedField(7, "temp_category", StringType(), required=False),
    NestedField(8, "is_daytime", BooleanType(), required=False),
)
```

---

## Step 2: Create the Ingestion Asset

Now let's fetch data from the OpenWeather API and store it in Iceberg.

### 2.1 Add API Configuration

Add your API configuration to `.phlo/.env.local`:

```bash
# Weather API Configuration
OPENWEATHER_API_KEY=your_api_key_here
OPENWEATHER_CITIES=London,GB;New York,US;Tokyo,JP;Sydney,AU
```

### 2.2 Create the Ingestion Asset

Create: `workflows/ingestion/weather_assets.py`

**Important:** We use **DLT (Data Load Tool)** for ingestion, following Phlo's established pattern. DLT handles:

- Robust data loading with retries
- Schema inference and validation
- Parquet file staging
- State management

```python
"""Weather data ingestion assets using DLT."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dagster as dg
import dlt
import os
import requests

from phlo_iceberg.settings import get_settings as get_iceberg_settings
from phlo_iceberg.resource import IcebergResource
from workflows.schemas.weather import WeatherObservationSchema
from workflows.schemas.iceberg_weather import WEATHER_OBSERVATION_SCHEMA


@dg.asset(
    name="dlt_weather_data",
    group_name="weather",
    compute_kind="dlt+pyiceberg",
    description="Fetches current weather data from OpenWeather API using DLT",
    retry_policy=dg.RetryPolicy(max_retries=3, delay=30),
)
def weather_data(
    context: dg.AssetExecutionContext,
    iceberg: IcebergResource,
) -> dg.MaterializeResult:
    """
    Ingest weather data using the two-step DLT pattern:

    1. Fetch data from OpenWeather API
    2. DLT stages data to parquet files
    3. PyIceberg registers/appends to Iceberg table

    Why DLT?
    - Handles schema evolution automatically
    - Robust error handling and retries
    - Consistent with other ingestion assets (for example dlt_events)
    - State management for incremental loads
    """
    iceberg_settings = get_iceberg_settings()
    table_name = f"{iceberg_settings.iceberg_default_namespace}.weather_observations"
    pipeline_name = "weather_openweathermap"

    # Setup DLT directories
    pipelines_base_dir = Path.home() / ".dlt" / "pipelines" / "weather"
    pipelines_base_dir.mkdir(parents=True, exist_ok=True)

    context.log.info(f"Starting weather data ingestion")
    context.log.info(f"Target table: {table_name}")

    try:
        # Step 1: Fetch data from OpenWeather API
        context.log.info("Fetching data from OpenWeather API...")

        openweather_cities = os.getenv("OPENWEATHER_CITIES", "")
        cities = [city.strip().split(",") for city in openweather_cities.split(";") if city.strip()]

        weather_records = []

        for city_name, country in cities:
            context.log.info(f"Fetching weather for {city_name}, {country}")

            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": f"{city_name},{country}",
                "appid": os.getenv("OPENWEATHER_API_KEY", ""),
                "units": "metric",  # Celsius
            }

            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                # Extract and structure data
                record = {
                    "city_name": data["name"],
                    "country": data["sys"]["country"],
                    "latitude": data["coord"]["lat"],
                    "longitude": data["coord"]["lon"],
                    "temperature": data["main"]["temp"],
                    "feels_like": data["main"]["feels_like"],
                    "humidity": data["main"]["humidity"],
                    "pressure": data["main"]["pressure"],
                    "wind_speed": data["wind"]["speed"],
                    "weather_main": data["weather"][0]["main"],
                    "weather_description": data["weather"][0]["description"],
                    "observation_time": datetime.fromtimestamp(data["dt"], tz=timezone.utc),
                    "sunrise_time": datetime.fromtimestamp(data["sys"]["sunrise"], tz=timezone.utc),
                    "sunset_time": datetime.fromtimestamp(data["sys"]["sunset"], tz=timezone.utc),
                }

                weather_records.append(record)

            except requests.RequestException as e:
                context.log.error(f"Failed to fetch {city_name}: {e}")
                continue

        if not weather_records:
            context.log.info("No weather data fetched, skipping")
            return dg.MaterializeResult(
                metadata={
                    "rows_loaded": dg.MetadataValue.int(0),
                    "status": dg.MetadataValue.text("no_data"),
                }
            )

        context.log.info(f"Successfully fetched {len(weather_records)} weather observations")

        # Add phlo ingestion timestamp
        ingestion_timestamp = datetime.now(timezone.utc)
        for record in weather_records:
            record["_cascade_ingested_at"] = ingestion_timestamp

        # Step 2: Stage to parquet using DLT
        context.log.info("Staging data to parquet via DLT...")
        start_time_ts = time.time()

        local_staging_root = (pipelines_base_dir / pipeline_name / "stage").resolve()
        local_staging_root.mkdir(parents=True, exist_ok=True)

        # Create DLT pipeline with filesystem destination targeting local staging
        filesystem_destination = dlt.destinations.filesystem(
            bucket_url=local_staging_root.as_uri(),
        )

        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            destination=filesystem_destination,
            dataset_name="weather",
            pipelines_dir=str(pipelines_base_dir),
        )

        # Define DLT resource
        @dlt.resource(name="weather_observations", write_disposition="replace")
        def provide_weather() -> Any:
            yield weather_records

        # Run DLT pipeline to stage parquet files
        info = pipeline.run(
            provide_weather(),
            loader_file_format="parquet",
        )

        if not info.load_packages:
            raise RuntimeError("DLT pipeline produced no load packages")

        # Get parquet file path
        load_package = info.load_packages[0]
        completed_jobs = load_package.jobs.get("completed_jobs") or []

        # Filter for actual parquet files (exclude pipeline state and other files)
        parquet_files = [job for job in completed_jobs if job.file_path.endswith('.parquet')]

        if not parquet_files:
            raise RuntimeError("DLT pipeline completed without producing parquet files")

        parquet_path = Path(parquet_files[0].file_path)
        if not parquet_path.is_absolute():
            parquet_path = (local_staging_root / parquet_path).resolve()

        dlt_elapsed = time.time() - start_time_ts
        context.log.info(f"DLT staging completed in {dlt_elapsed:.2f}s")

        # Step 3: Ensure Iceberg table exists
        context.log.info(f"Ensuring Iceberg table {table_name} exists...")
        iceberg.ensure_table(
            table_name=table_name,
            schema=WEATHER_OBSERVATION_SCHEMA,
            partition_spec=None,
        )

        # Step 4: Append to Iceberg table
        context.log.info("Appending data to Iceberg table...")
        iceberg.append_parquet(
            table_name=table_name,
            data_path=str(parquet_path),
        )

        total_elapsed = time.time() - start_time_ts
        rows_loaded = len(weather_records)
        context.log.info(f"Ingestion completed successfully in {total_elapsed:.2f}s")
        context.log.info(f"Loaded {rows_loaded} rows to {table_name}")

        return dg.MaterializeResult(
            metadata={
                "rows_loaded": dg.MetadataValue.int(rows_loaded),
                "cities": dg.MetadataValue.text(", ".join([r["city_name"] for r in weather_records])),
                "table_name": dg.MetadataValue.text(table_name),
                "dlt_elapsed_seconds": dg.MetadataValue.float(dlt_elapsed),
                "total_elapsed_seconds": dg.MetadataValue.float(total_elapsed),
            }
        )

    except requests.RequestException as e:
        context.log.error(f"API request failed: {e}")
        raise RuntimeError(f"Failed to fetch data from OpenWeather API") from e

    except Exception as e:
        context.log.error(f"Ingestion failed: {e}")
        raise RuntimeError(f"Weather data ingestion failed: {e}") from e


def build_weather_ingestion_defs() -> dg.Definitions:
    """Build definitions for weather ingestion assets."""
    return dg.Definitions(
        assets=[weather_data],
    )
```

### 2.4 Registration

No manual registration is needed. Phlo discovers all Python files under `workflows/` automatically.

### 2.5 Test the Ingestion

```bash
# Reload Dagster
phlo services restart --service dagster

# Open Dagster UI
# Navigate to Assets → dlt_weather_data
# Click "Materialize"

# Or use CLI
dagster asset materialize -m phlo_dagster.framework.definitions -a dlt_weather_data
```

**What just happened?**

1. Fetched weather data from the OpenWeather API
2. DLT staged the data to local parquet files with schema validation
3. PyIceberg appended the parquet to the Iceberg table
4. Nessie catalog updated with the new snapshot

**Why DLT?**

- Consistent with the generated ingestion asset pattern (`dlt_events`)
- Handles schema evolution automatically
- Robust parquet file generation with proper typing
- State management for incremental loads
- Better separation of concerns (fetch → DLT stage → PyIceberg register)
- Matches established Phlo pattern for all ingestion assets

**Verify:**

```sql
-- Connect to Trino
docker-compose exec trino trino

-- Query the data
SELECT * FROM iceberg.raw.weather_observations;
```

---

## Step 3: Create Bronze Layer (Staging)

The Bronze layer cleans and standardizes raw data.

### 3.1 Define the Source

Create: `workflows/transforms/dbt/models/sources/sources_weather.yml`

```yaml
version: 2

sources:
  - name: raw
    description: "Raw data layer - data as ingested from sources"
    schema: raw

    tables:
      - name: weather_observations
        description: "Raw weather observations from OpenWeather API"
        columns:
          - name: city_name
            description: "City name"
            tests:
              - not_null

          - name: observation_time
            description: "When the observation was recorded"
            tests:
              - not_null

          - name: temperature
            description: "Temperature in Celsius"
            tests:
              - not_null
```

### 3.2 Create the Staging Model

Create: `workflows/transforms/dbt/models/bronze/stg_weather_observations.sql`

```sql
{{
    config(
        materialized='table',
        schema='bronze',
        tags=['weather', 'bronze']
    )
}}

WITH source AS (
    SELECT * FROM {{ source('raw', 'weather_observations') }}
),

cleaned AS (
    SELECT
        -- Identifiers
        city_name,
        country,
        ROUND(CAST(latitude AS DOUBLE), 4) AS latitude,
        ROUND(CAST(longitude AS DOUBLE), 4) AS longitude,

        -- Weather measurements
        ROUND(CAST(temperature AS DOUBLE), 2) AS temperature_c,
        ROUND(CAST(feels_like AS DOUBLE), 2) AS feels_like_c,
        CAST(humidity AS INTEGER) AS humidity_pct,
        CAST(pressure AS INTEGER) AS pressure_hpa,
        ROUND(CAST(wind_speed AS DOUBLE), 2) AS wind_speed_ms,

        -- Conditions
        LOWER(TRIM(weather_main)) AS weather_main,
        LOWER(TRIM(weather_description)) AS weather_description,

        -- Timestamps
        CAST(observation_time AS TIMESTAMP) AS observation_timestamp,
        CAST(sunrise_time AS TIMESTAMP) AS sunrise_timestamp,
        CAST(sunset_time AS TIMESTAMP) AS sunset_timestamp,

        -- Metadata
        DATE(observation_time) AS observation_date,
        HOUR(observation_time) AS observation_hour

    FROM source
    WHERE
        -- Data quality filters
        temperature IS NOT NULL
        AND observation_time IS NOT NULL
        AND city_name IS NOT NULL
        -- Remove outliers
        AND temperature BETWEEN -50 AND 60  -- Reasonable temp range
        AND humidity BETWEEN 0 AND 100
        AND wind_speed >= 0
)

SELECT * FROM cleaned
```

**What this does:**

- Standardizes column names (e.g., `temperature` → `temperature_c`)
- Converts types explicitly
- Rounds numeric values
- Standardizes text (lowercase, trim)
- Adds derived date/hour fields
- Filters out invalid data
- Removes outliers

### 3.3 Add Tests

Create: `workflows/transforms/dbt/models/bronze/schema.yml`

```yaml
version: 2

models:
  - name: stg_weather_observations
    description: "Cleaned and standardized weather observations"
    columns:
      - name: city_name
        description: "City name"
        tests:
          - not_null

      - name: temperature_c
        description: "Temperature in Celsius"
        tests:
          - not_null
          - dbt_utils.accepted_range:
              min_value: -50
              max_value: 60

      - name: humidity_pct
        description: "Humidity percentage"
        tests:
          - not_null
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 100

      - name: observation_timestamp
        description: "Observation timestamp"
        tests:
          - not_null

      - name: observation_date
        description: "Observation date"
        tests:
          - not_null
```

---

## Step 4: Create Silver Layer (Facts)

The Silver layer adds business logic and calculated fields.

### 4.1 Create the Fact Table

Create: `workflows/transforms/dbt/models/silver/fct_weather_readings.sql`

```sql
{{
    config(
        materialized='table',
        schema='silver',
        tags=['weather', 'silver', 'fact']
    )
}}

WITH staged AS (
    SELECT * FROM {{ ref('stg_weather_observations') }}
),

enriched AS (
    SELECT
        -- All original fields
        *,

        -- Temperature conversions
        ROUND((temperature_c * 9.0 / 5.0) + 32, 2) AS temperature_f,
        ROUND((feels_like_c * 9.0 / 5.0) + 32, 2) AS feels_like_f,

        -- Temperature category
        CASE
            WHEN temperature_c < 0 THEN 'Freezing'
            WHEN temperature_c < 10 THEN 'Cold'
            WHEN temperature_c < 20 THEN 'Mild'
            WHEN temperature_c < 30 THEN 'Warm'
            ELSE 'Hot'
        END AS temp_category,

        -- Comfort index (simplified)
        CASE
            WHEN humidity_pct > 80 AND temperature_c > 25 THEN 'Uncomfortable'
            WHEN humidity_pct < 30 AND temperature_c < 10 THEN 'Dry-Cold'
            ELSE 'Comfortable'
        END AS comfort_level,

        -- Wind category (Beaufort scale simplified)
        CASE
            WHEN wind_speed_ms < 0.3 THEN 'Calm'
            WHEN wind_speed_ms < 3.3 THEN 'Light'
            WHEN wind_speed_ms < 7.9 THEN 'Moderate'
            WHEN wind_speed_ms < 13.8 THEN 'Fresh'
            ELSE 'Strong'
        END AS wind_category,

        -- Daylight indicator
        CASE
            WHEN observation_timestamp BETWEEN sunrise_timestamp AND sunset_timestamp
            THEN TRUE
            ELSE FALSE
        END AS is_daytime,

        -- Daylight duration (hours)
        ROUND(
            CAST(sunset_timestamp AS BIGINT) - CAST(sunrise_timestamp AS BIGINT)
        ) / 3600.0 AS daylight_hours,

        -- Weather condition grouping
        CASE
            WHEN weather_main IN ('rain', 'drizzle', 'thunderstorm') THEN 'Precipitation'
            WHEN weather_main IN ('snow', 'sleet') THEN 'Winter Precipitation'
            WHEN weather_main IN ('clear', 'clouds') THEN 'Dry'
            WHEN weather_main IN ('fog', 'mist', 'haze') THEN 'Low Visibility'
            ELSE 'Other'
        END AS weather_category,

        -- Location identifier (for grouping)
        city_name || ', ' || country AS location_key

    FROM staged
)

SELECT * FROM enriched
```

**What this does:**

- Converts Celsius to Fahrenheit
- Categorizes temperature (Freezing/Cold/Mild/Warm/Hot)
- Calculates comfort level
- Categorizes wind speed
- Determines if daytime or nighttime
- Calculates daylight duration
- Groups weather conditions
- Creates location key for grouping

### 4.2 Add Tests

Create: `workflows/transforms/dbt/models/silver/schema.yml`

```yaml
version: 2

models:
  - name: fct_weather_readings
    description: "Weather readings with calculated metrics and categorizations"
    columns:
      - name: location_key
        description: "Unique location identifier"
        tests:
          - not_null

      - name: temperature_f
        description: "Temperature in Fahrenheit"
        tests:
          - not_null

      - name: temp_category
        description: "Temperature category"
        tests:
          - not_null
          - accepted_values:
              values: ["Freezing", "Cold", "Mild", "Warm", "Hot"]

      - name: is_daytime
        description: "True if observation during daylight"
        tests:
          - not_null
```

---

## Step 5: Create Gold Layer (Aggregations)

The Gold layer creates aggregated, business-ready datasets.

### 5.1 Create Daily Summary

Create: `workflows/transforms/dbt/models/gold/agg_daily_weather_summary.sql`

```sql
{{
    config(
        materialized='table',
        schema='gold',
        tags=['weather', 'gold', 'aggregation']
    )
}}

WITH weather_readings AS (
    SELECT * FROM {{ ref('fct_weather_readings') }}
),

daily_stats AS (
    SELECT
        observation_date,
        location_key,
        city_name,
        country,

        -- Temperature statistics
        COUNT(*) AS event_count,
        ROUND(AVG(temperature_c), 2) AS avg_temp_c,
        ROUND(MIN(temperature_c), 2) AS min_temp_c,
        ROUND(MAX(temperature_c), 2) AS max_temp_c,
        ROUND(AVG(temperature_f), 2) AS avg_temp_f,

        -- Feels like temperature
        ROUND(AVG(feels_like_c), 2) AS avg_feels_like_c,

        -- Humidity statistics
        ROUND(AVG(humidity_pct), 2) AS avg_humidity_pct,
        MIN(humidity_pct) AS min_humidity_pct,
        MAX(humidity_pct) AS max_humidity_pct,

        -- Wind statistics
        ROUND(AVG(wind_speed_ms), 2) AS avg_wind_speed_ms,
        ROUND(MAX(wind_speed_ms), 2) AS max_wind_speed_ms,

        -- Pressure statistics
        ROUND(AVG(pressure_hpa), 2) AS avg_pressure_hpa,

        -- Daylight info (should be consistent per day)
        AVG(daylight_hours) AS daylight_hours,

        -- Weather conditions (most common)
        MODE() WITHIN GROUP (ORDER BY weather_main) AS predominant_weather,
        MODE() WITHIN GROUP (ORDER BY temp_category) AS predominant_temp_category,

        -- Comfort analysis
        SUM(CASE WHEN comfort_level = 'Comfortable' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_comfortable,

        -- Precipitation indicator
        MAX(CASE WHEN weather_category = 'Precipitation' THEN 1 ELSE 0 END) AS had_precipitation

    FROM weather_readings
    GROUP BY
        observation_date,
        location_key,
        city_name,
        country
)

SELECT * FROM daily_stats
ORDER BY observation_date DESC, location_key
```

### 5.2 Create Location Dimension

Create: `workflows/transforms/dbt/models/gold/dim_location.sql`

```sql
{{
    config(
        materialized='table',
        schema='gold',
        tags=['weather', 'gold', 'dimension']
    )
}}

WITH locations AS (
    SELECT DISTINCT
        location_key,
        city_name,
        country,
        latitude,
        longitude
    FROM {{ ref('stg_weather_observations') }}
)

SELECT
    ROW_NUMBER() OVER (ORDER BY location_key) AS location_id,
    location_key,
    city_name,
    country,
    latitude,
    longitude,
    -- Add region/continent based on country (simplified)
    CASE
        WHEN country IN ('US', 'CA', 'MX') THEN 'North America'
        WHEN country IN ('GB', 'FR', 'DE', 'IT', 'ES') THEN 'Europe'
        WHEN country IN ('JP', 'CN', 'IN', 'KR') THEN 'Asia'
        WHEN country IN ('AU', 'NZ') THEN 'Oceania'
        ELSE 'Other'
    END AS region
FROM locations
```

---

## Step 6: Create Marts for BI

Marts are optimized for BI tools and end-user queries.

### 6.1 Create Weather Overview Mart

Create: `workflows/transforms/dbt/models/marts_postgres/mrt_weather_overview.sql`

```sql
{{
    config(
        materialized='table',
        schema='marts',
        tags=['weather', 'marts', 'postgres']
    )
}}

WITH daily_summary AS (
    SELECT * FROM {{ ref('agg_daily_weather_summary') }}
),

location_dim AS (
    SELECT * FROM {{ ref('dim_location') }}
),

enriched AS (
    SELECT
        d.observation_date,
        d.location_key,
        d.city_name,
        d.country,
        l.region,

        -- Temperature metrics
        d.avg_temp_c,
        d.min_temp_c,
        d.max_temp_c,
        d.avg_temp_f,
        d.avg_feels_like_c,
        d.max_temp_c - d.min_temp_c AS temp_range_c,

        -- Other metrics
        d.avg_humidity_pct,
        d.avg_wind_speed_ms,
        d.avg_pressure_hpa,
        d.daylight_hours,

        -- Weather conditions
        d.predominant_weather,
        d.predominant_temp_category,
        d.pct_comfortable,
        d.had_precipitation,

        -- Metadata
        d.event_count,
        CURRENT_TIMESTAMP AS last_updated

    FROM daily_summary d
    LEFT JOIN location_dim l ON d.location_key = l.location_key
)

SELECT * FROM enriched
ORDER BY observation_date DESC, city_name
```

### 6.2 Create Recent Readings Mart

Create: `workflows/transforms/dbt/models/marts_postgres/mrt_recent_weather.sql`

```sql
{{
    config(
        materialized='table',
        schema='marts',
        tags=['weather', 'marts', 'postgres']
    )
}}

WITH recent_readings AS (
    SELECT
        location_key,
        city_name,
        country,
        temperature_c,
        temperature_f,
        feels_like_c,
        humidity_pct,
        wind_speed_ms,
        weather_main,
        weather_description,
        temp_category,
        comfort_level,
        is_daytime,
        observation_timestamp,

        -- Rank by recency per location
        ROW_NUMBER() OVER (
            PARTITION BY location_key
            ORDER BY observation_timestamp DESC
        ) AS recency_rank

    FROM {{ ref('fct_weather_readings') }}
    WHERE observation_timestamp >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
)

SELECT
    location_key,
    city_name,
    country,
    temperature_c,
    temperature_f,
    feels_like_c,
    humidity_pct,
    wind_speed_ms,
    weather_main,
    weather_description,
    temp_category,
    comfort_level,
    is_daytime,
    observation_timestamp,
    CURRENT_TIMESTAMP AS refreshed_at
FROM recent_readings
WHERE recency_rank = 1  -- Most recent reading per location
ORDER BY city_name
```

---

## Step 7: Add Data Quality Checks

Quality checks ensure your data meets expectations.

### 7.1 Create Quality Check Asset

Create: `workflows/quality/weather.py`

```python
"""Data quality checks for weather pipeline."""

import dagster as dg
import pandas as pd
from dagster_pandera import pandera_schema_to_dagster_type

from workflows.schemas.weather import WeatherObservationSchema, WeatherReadingSchema
from phlo_trino.resource import TrinoResource


@dg.asset(
    name="weather_quality_check_raw",
    group_name="weather_quality",
    deps=["weather_data"],
    description="Quality checks for raw weather data",
)
def check_raw_weather_quality(
    context: dg.AssetExecutionContext,
    trino: TrinoResource,
) -> dg.MaterializeResult:
    """
    Perform quality checks on raw weather data.

    Checks:
    - Record count
    - Null values
    - Value ranges
    - Duplicate detection
    """
    query = """
    SELECT
        COUNT(*) as total_records,
        COUNT(DISTINCT city_name) as unique_cities,
        SUM(CASE WHEN temperature IS NULL THEN 1 ELSE 0 END) as null_temperatures,
        MIN(temperature) as min_temp,
        MAX(temperature) as max_temp,
        MIN(observation_time) as earliest_observation,
        MAX(observation_time) as latest_observation
    FROM iceberg.raw.weather_observations
    """

    result = trino.execute(query)
    row = result[0]

    # Assertions
    assert row["total_records"] > 0, "No records found"
    assert row["unique_cities"] > 0, "No cities found"
    assert row["null_temperatures"] == 0, f"Found {row['null_temperatures']} null temperatures"
    assert -50 <= row["min_temp"] <= 60, f"Temperature out of range: {row['min_temp']}"
    assert -50 <= row["max_temp"] <= 60, f"Temperature out of range: {row['max_temp']}"

    context.log.info(f"✓ Quality checks passed")
    context.log.info(f"  Total records: {row['total_records']}")
    context.log.info(f"  Unique cities: {row['unique_cities']}")
    context.log.info(f"  Temperature range: {row['min_temp']}°C to {row['max_temp']}°C")

    return dg.MaterializeResult(
        metadata={
            "total_records": row["total_records"],
            "unique_cities": row["unique_cities"],
            "min_temp": row["min_temp"],
            "max_temp": row["max_temp"],
        }
    )


@dg.asset(
    name="weather_quality_check_silver",
    group_name="weather_quality",
    deps=["dbt:fct_weather_readings"],
    description="Quality checks for silver layer weather data",
)
def check_silver_weather_quality(
    context: dg.AssetExecutionContext,
    trino: TrinoResource,
) -> dg.MaterializeResult:
    """
    Perform quality checks on silver layer weather data.

    Checks:
    - All calculated fields present
    - Categories valid
    - No nulls in required fields
    """
    query = """
    SELECT
        COUNT(*) as total_records,
        SUM(CASE WHEN temp_category IS NULL THEN 1 ELSE 0 END) as null_categories,
        SUM(CASE WHEN temperature_f IS NULL THEN 1 ELSE 0 END) as null_temp_f,
        COUNT(DISTINCT temp_category) as distinct_categories
    FROM iceberg.silver.fct_weather_readings
    """

    result = trino.execute(query)
    row = result[0]

    # Assertions
    assert row["null_categories"] == 0, f"Found {row['null_categories']} null categories"
    assert row["null_temp_f"] == 0, f"Found {row['null_temp_f']} null Fahrenheit temps"
    assert row["distinct_categories"] == 5, f"Expected 5 categories, found {row['distinct_categories']}"

    context.log.info(f"✓ Silver layer quality checks passed")

    return dg.MaterializeResult(
        metadata={
            "total_records": row["total_records"],
            "distinct_categories": row["distinct_categories"],
        }
    )


def build_weather_quality_defs() -> dg.Definitions:
    """Build quality check definitions."""
    return dg.Definitions(
        assets=[
            check_raw_weather_quality,
            check_silver_weather_quality,
        ],
    )
```

### 7.2 Quality Check Discovery

No manual registration is needed. Phlo discovers quality workflows under `workflows/quality/`.

---

## Step 8: Configure Publishing

Configure which tables get published to PostgreSQL for BI tools.

### 8.1 Update Publishing Config

Edit: `workflows/publishing/config.yaml`

```yaml
# Existing configurations...

weather:
  description: "Weather data marts for BI dashboards"
  enabled: true
  tables:
    - iceberg_table: "marts.mrt_weather_overview"
      postgres_table: "mrt_weather_overview"
      postgres_schema: "marts"
      description: "Daily weather summary by location"
      mode: "replace" # Options: replace, append, upsert

    - iceberg_table: "marts.mrt_recent_weather"
      postgres_table: "mrt_recent_weather"
      postgres_schema: "marts"
      description: "Most recent weather reading per location"
      mode: "replace"
```

### 8.2 Create Publishing Asset

The publishing asset is already generic and will pick up your config automatically. Just ensure it runs after your dbt models.

Test it:

```bash
dagster asset materialize -m phlo_dagster.framework.definitions -a publish_postgres_marts
```

---

## Step 9: Add Scheduling

Schedule your pipeline to run automatically.

### 9.1 Create Schedule

Edit: `workflows/schedules/schedules.py`

```python
# Add to existing schedules

@dg.schedule(
    name="weather_pipeline_schedule",
    cron_schedule="0 * * * *",  # Every hour
    job=dg.define_asset_job(
        name="weather_pipeline_job",
        selection=dg.AssetSelection.groups("weather"),  # All weather assets
    ),
    default_status=dg.DefaultScheduleStatus.RUNNING,  # Auto-start
    execution_timezone="UTC",
)
def weather_pipeline_schedule():
    """Run weather pipeline every hour."""
    return dg.RunRequest()
```

### 9.2 Add Sensor for Freshness

Create a sensor that alerts if data gets stale:

```python
@dg.sensor(
    name="weather_freshness_sensor",
    minimum_interval_seconds=300,  # Check every 5 minutes
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def weather_freshness_sensor(context: dg.SensorEvaluationContext):
    """Alert if weather data is stale (> 2 hours old)."""

    # Check last materialization time
    weather_asset_key = dg.AssetKey(["weather_data"])
    latest_materialization = context.instance.get_latest_materialization_event(weather_asset_key)

    if not latest_materialization:
        context.log.warning("No materialization found for weather_data")
        return dg.SkipReason("No materialization yet")

    from datetime import datetime, timedelta

    last_update = latest_materialization.timestamp
    age = datetime.now().timestamp() - last_update
    age_hours = age / 3600

    if age_hours > 2:
        context.log.error(f"Weather data is {age_hours:.1f} hours old!")
        # Trigger re-materialization
        return dg.RunRequest(
            asset_selection=[weather_asset_key],
        )

    return dg.SkipReason(f"Data is fresh ({age_hours:.1f} hours old)")
```

---

## Step 10: Test and Deploy

### 10.1 Run Complete Pipeline

```bash
# Materialize all weather assets
dagster asset materialize -m phlo_dagster.framework.definitions \
  --select "tag:weather"

# Or use Dagster UI:
# 1. Open http://localhost:3000
# 2. Assets → Filter by tag: weather
# 3. Select all → Materialize
```

### 10.2 Verify Each Layer

```sql
-- Check raw data
SELECT COUNT(*), MIN(observation_time), MAX(observation_time)
FROM iceberg.raw.weather_observations;

-- Check bronze data
SELECT COUNT(*) FROM iceberg.bronze.stg_weather_observations;

-- Check silver data
SELECT
    city_name,
    temperature_c,
    temp_category,
    comfort_level
FROM iceberg.silver.fct_weather_readings
ORDER BY observation_timestamp DESC
LIMIT 10;

-- Check gold data
SELECT * FROM iceberg.gold.agg_daily_weather_summary
ORDER BY observation_date DESC;

-- Check marts (in PostgreSQL)
SELECT * FROM marts.mrt_weather_overview
ORDER BY observation_date DESC;
```

### 10.3 Run dbt Tests

```bash
docker-compose exec dagster-webserver \
  dbt test --project-dir /app/workflows/transforms/dbt \
  --select tag:weather
```

### 10.4 Create Superset Dashboard

1. Open Superset: http://localhost:10008
2. Add PostgreSQL database connection
3. Create dataset from `marts.mrt_weather_overview`
4. Create charts:
   - Line chart: Temperature trends by city
   - Bar chart: Current temperatures
   - Table: Recent readings
5. Add to dashboard

---

## Advanced Patterns

### Pattern 1: Incremental Processing

Instead of reprocessing all data, process only new data:

```sql
{{
    config(
        materialized='incremental',
        unique_key='observation_timestamp',
        schema='silver'
    )
}}

SELECT * FROM {{ ref('stg_weather_observations') }}

{% if is_incremental() %}
    -- Only new data since last run
    WHERE observation_timestamp > (SELECT MAX(observation_timestamp) FROM {{ this }})
{% endif %}
```

### Pattern 2: Partitioned Assets

Partition by date for better performance:

```python
from dagster import DailyPartitionsDefinition

daily_partition = DailyPartitionsDefinition(start_date="2024-01-01")

@dg.asset(
    partitions_def=daily_partition,
    name="weather_data_partitioned",
)
def fetch_weather_data_partitioned(context: dg.AssetExecutionContext):
    partition_date = context.partition_key  # "2024-11-05"
    # Fetch only data for this date
    ...
```

### Pattern 3: Dynamic Cities

Fetch list of cities from a config table instead of hardcoding:

```python
@dg.asset
def weather_cities_config(trino: TrinoResource):
    """Fetch cities to monitor from config table."""
    query = "SELECT city_name, country FROM config.weather_cities WHERE active = true"
    return trino.execute(query)

@dg.asset
def weather_data(weather_cities_config):
    """Fetch weather for dynamic list of cities."""
    cities = weather_cities_config
    # Fetch for each city...
```

### Pattern 4: Error Handling

Add retry logic and error notifications:

```python
@dg.asset(
    retry_policy=dg.RetryPolicy(
        max_retries=3,
        delay=30,  # seconds
    ),
    op_tags={"notify_on_failure": "true"},
)
def weather_data_with_retry(...):
    """Fetch with automatic retries."""
    ...
```

### Pattern 5: Caching

Cache expensive operations:

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def get_api_client():
    """Cached API client."""
    return OpenWeatherClient(api_key=config.openweather_api_key)
```

---

### Pattern 6: Contract Typing and DLT Staging

Contracts are evaluated **after** DLT stages your data to Parquet. DLT
normalizes ISO-8601 strings to timestamps during that staging, so a source
field that arrives as `"2026-08-20T05:00:00Z"` reaches validation as a
timestamp. Type temporal contract fields natively or validation will reject
the normalized values:

```python
class ReadingSchema(pa.DataFrameModel):
    event_time: Series[datetime]   # not Series[str]
```

Columns present in the staged data but missing from the contract are dropped
before the table-store write. Phlo logs a `source_columns_dropped_by_contract`
warning and records them in the run's `dropped_source_columns` metadata -
add such fields to the contract (or remove them from the source) instead of
letting them disappear.

### Pattern 7: Unpartitioned Reference Sources

Reference-style sources (small lookup tables merged wholesale) do not need a
partition. Declare them with `partitioned=False` so runs never require a
partition key; the source function receives an empty string for
`partition_date`:

```python
@phlo_ingestion(
    table_name="device_registry",
    unique_key="device_id",
    group="registry",
    validation_schema=DeviceSchema,
    partitioned=False,
)
def load_registry(partition_date: str):
    del partition_date  # registry merges ignore partitions
    ...
```

### Pattern 8: Blocking Domain Quality Checks

Contract checks and dbt tests gate publication; plain helper functions in
your quality modules do not - they only run when something calls them. To make
a domain rule block an ingestion run, pass it via `quality_checks`. Each
callable receives the staged dataframe, returns `None` on success or a
violation description, and becomes a blocking asset check under
`strict_validation=True`:

```python
def assert_paid_within_allowed(readings: pd.DataFrame) -> str | None:
    if (readings.paid_amount > readings.allowed_amount).any():
        return "paid amount exceeds allowed amount"
    return None


@phlo_ingestion(..., quality_checks=[assert_paid_within_allowed])
def load_claims(partition_date: str): ...
```

---

## Next Steps

🎉 **Congratulations!** You've built a complete data pipeline from scratch.

**What you've learned:**

- ✅ Define schemas
- ✅ Create ingestion assets
- ✅ Build Bronze/Silver/Gold layers with dbt
- ✅ Add data quality checks
- ✅ Publish to PostgreSQL
- ✅ Schedule automated runs
- ✅ Create BI dashboards

**Continue learning:**

- [Data Modeling Guide](data-modeling.md) - Best practices for schema design
- [Dagster Assets Tutorial](dagster-assets.md) - Advanced orchestration patterns
- [dbt Development Guide](dbt-development.md) - Advanced SQL techniques
- [Troubleshooting Guide](../operations/troubleshooting.md) - Debug common issues

**Try these challenges:**

1. Add more cities to monitor
2. Create hourly aggregations
3. Add weather alerts (e.g., temperature > 35°C)
4. Integrate with another API
5. Create predictive features for ML

**Happy data engineering!** 🌤️
