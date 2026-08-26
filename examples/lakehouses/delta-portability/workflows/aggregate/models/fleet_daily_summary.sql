-- Daily per-site fleet summary. Coverage is measured against registered
-- active devices so silent fleet shrinkage becomes visible as completeness.
with reporting as (
    select
        h.site_id,
        count(distinct h.device_id) as devices_reporting,
        cast(h.event_hour as date) as report_date,
        round(avg(h.avg_temperature_c), 3) as fleet_avg_temperature_c,
        sum(h.late_reading_count) as late_readings,
        sum(h.flagged_readings) as flagged_readings,
        sum(h.reading_count) as reading_count
    from {{ ref('device_health_hourly') }} as h
    group by h.site_id, cast(h.event_hour as date)
),
registered as (
    select
        site_id,
        count(*) as active_devices
    from {{ ref('stg_devices') }}
    where decommissioned_at is null
    group by site_id
)
select
    r.site_id || '|' || cast(r.report_date as varchar) as site_day_key,
    r.site_id,
    r.report_date,
    g.active_devices,
    r.devices_reporting,
    round(cast(r.devices_reporting as double) / g.active_devices, 4) as completeness_ratio,
    r.reading_count,
    r.fleet_avg_temperature_c,
    r.late_readings,
    r.flagged_readings
from reporting as r
join registered as g on g.site_id = r.site_id
