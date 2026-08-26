-- Monthly climate indicators per station: mean temperature on the normalized
-- Celsius scale and total precipitation at monthly grain.
select
    station_id,
    obs_month,
    count(*) as observation_count,
    avg(temp_c) as avg_temp_c,
    sum(precip_mm) as precip_mm_total,
    max(pressure_hpa) as max_pressure_hpa
from {{ ref('stg_observations') }}
group by
    station_id,
    obs_month
