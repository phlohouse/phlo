-- Curated daily site report: fleet coverage joined with site reference data
-- and the region lookup that arrived through the PostgreSQL Sling stream.
select
    s.site_day_key,
    s.report_date,
    s.site_id,
    p.site_name,
    p.region,
    g.region_name,
    g.country,
    s.active_devices,
    s.devices_reporting,
    s.completeness_ratio,
    s.reading_count,
    s.fleet_avg_temperature_c,
    s.late_readings,
    s.flagged_readings
from {{ ref('fleet_daily_summary') }} as s
join {{ ref('stg_sites') }} as p on p.site_id = s.site_id
join {{ ref('stg_regions') }} as g on g.region_code = p.region
