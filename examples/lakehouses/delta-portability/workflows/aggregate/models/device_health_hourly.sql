-- Hourly device health over deduplicated readings. Rebuilding this model
-- after a correction or late arrival repairs the hour without touching raw.
select
    device_id || '|' || date_format(event_hour, '%Y-%m-%dT%H') as health_key,
    device_id,
    site_id,
    event_hour,
    count(*) as reading_count,
    min(sequence_number) as min_sequence_number,
    max(sequence_number) as max_sequence_number,
    round(avg(temperature_c), 3) as avg_temperature_c,
    round(max(temperature_c), 3) as max_temperature_c,
    round(avg(humidity_pct), 3) as avg_humidity_pct,
    round(min(battery_pct), 3) as min_battery_pct,
    sum(case when arrived_late then 1 else 0 end) as late_reading_count,
    sum(case when temperature_c >= 45.0 or battery_pct <= 15.0 then 1 else 0 end)
        as flagged_readings
from {{ ref('telemetry_dedup') }}
group by device_id, site_id, event_hour
