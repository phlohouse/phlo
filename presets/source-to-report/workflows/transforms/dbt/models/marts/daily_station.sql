-- One row per station, pollutant and day. Daily means are what the 24-hour
-- objective is judged on; the daily maximum feeds the 1-hour NO2 check.
{{ config(materialized='table') }}

select
    observed_date,
    station_id,
    station_name,
    pollutant,
    avg(value) as mean_value,
    max(value) as max_value,
    count(*) as readings
from {{ ref('stg_readings') }}
group by observed_date, station_id, station_name, pollutant
