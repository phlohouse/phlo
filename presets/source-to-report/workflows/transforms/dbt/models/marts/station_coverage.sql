-- How much of the year each station and pollutant actually covers. A gap is
-- visible here before it becomes a quiet understatement in the report.
{{ config(materialized='table') }}

select
    station_id,
    station_name,
    pollutant,
    count(*) as observed_hours,
    {{ var('expected_hours') }} as expected_hours,
    round(count(*) * 1.0 / {{ var('expected_hours') }}, 4) as coverage
from {{ ref('stg_readings') }}
group by station_id, station_name, pollutant
