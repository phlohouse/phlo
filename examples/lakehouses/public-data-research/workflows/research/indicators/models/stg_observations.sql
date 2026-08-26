-- Staging for climate indicators: normalizes the mixed-unit archive into one
-- Celsius scale. Fahrenheit-flagged rows store raw Fahrenheit in temp_c and
-- are converted exactly here; pressure_hpa is nullable because archives
-- before July 2026 do not report it (schema drift).
select
    station_id,
    observed_at,
    obs_month,
    case when unit_f then (temp_c - 32.0) * 5.0 / 9.0 else temp_c end as temp_c,
    unit_f,
    precip_mm,
    pressure_hpa
from {{ source('public_raw', 'weather_observations') }}
