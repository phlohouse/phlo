-- Consumer-facing current health: the newest hourly health row per device.
-- Rebuilding after a rolling repair moves devices onto repaired hours only.
with ranked as (
    select
        health_key,
        device_id,
        event_hour,
        reading_count,
        min_sequence_number,
        max_sequence_number,
        avg_temperature_c,
        max_temperature_c,
        avg_humidity_pct,
        min_battery_pct,
        late_reading_count,
        flagged_readings,
        row_number() over (partition by device_id order by event_hour desc) as recency
    from {{ ref('device_health_hourly') }}
)
select
    health_key,
    device_id,
    event_hour,
    reading_count,
    min_sequence_number,
    max_sequence_number,
    avg_temperature_c,
    max_temperature_c,
    avg_humidity_pct,
    min_battery_pct,
    late_reading_count,
    flagged_readings
from ranked
where recency = 1
