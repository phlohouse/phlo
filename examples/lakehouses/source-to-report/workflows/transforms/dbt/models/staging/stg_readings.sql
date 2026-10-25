-- Hourly readings, typed and limited to the reporting period.
{{ config(materialized='view') }}

select
    reading_id,
    station_id,
    station_name,
    pollutant,
    -- Kept as the source's ISO string: Iceberg only accepts timestamp(6)/(9),
    -- and this column is evidence, not a join key.
    observed_at,
    cast(observed_date as date) as observed_date,
    cast(value as double) as value,
    unit
from {{ source('raw', 'air_readings') }}
where cast(observed_date as date) between cast('{{ var("period_start") }}' as date)
                                      and cast('{{ var("period_end") }}' as date)
