-- Annual means per station and pollutant, compared with the annual objective
-- in the report rather than here.
{{ config(materialized='table') }}

select
    station_id,
    station_name,
    pollutant,
    avg(value) as mean_value,
    max(value) as max_value,
    count(*) as readings,
    station_id || '-' || pollutant as station_key
from {{ ref('stg_readings') }}
group by station_id, station_name, pollutant
