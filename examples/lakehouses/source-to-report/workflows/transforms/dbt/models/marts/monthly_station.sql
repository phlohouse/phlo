-- Monthly means per station and pollutant: the evidence behind every
-- published monthly figure.
{{ config(materialized='table') }}

select
    cast(date_trunc('month', observed_date) as date) as month,
    station_id,
    station_name,
    pollutant,
    avg(value) as mean_value,
    max(value) as max_value,
    count(*) as readings,
    station_id || '-' || pollutant || '-' || cast(cast(date_trunc('month', observed_date) as date) as varchar) as month_key
from {{ ref('stg_readings') }}
group by 1, 2, 3, 4
