-- Days that breach the pollutant's short-term objective. PM2.5 is judged on
-- the 24-hour mean; NO2 is judged on the 1-hour mean, so the daily maximum is
-- the right column for it.
{{ config(materialized='table') }}

select
    cast(date_trunc('month', observed_date) as date) as month,
    station_id,
    station_name,
    pollutant,
    count(*) filter (
        where (pollutant = 'PM2.5' and mean_value > {{ var('pm25_daily_objective') }})
           or (pollutant = 'NO2' and max_value > {{ var('no2_daily_objective') }})
    ) as days_over
from {{ ref('daily_station') }}
group by 1, 2, 3, 4
