-- Deduplication and late-event correction.
--
-- Raw telemetry is append-only, so retransmitted deliveries accumulate as
-- extra versions of a message. This model keeps exactly one version per
-- message_id (newest ingestion wins) and overlays merged corrections on top,
-- so reprocessing repairs aggregates without duplicating raw events.
with versions as (
    select
        r.*,
        row_number() over (
            partition by r.message_id
            order by r._phlo_ingested_at desc, r._phlo_row_id desc
        ) as version_rank
    from {{ source('delta_raw', 'telemetry_readings') }} as r
),
corrections as (
    select
        message_id,
        corrected_temperature_c,
        corrected_humidity_pct,
        correction_reason
    from {{ source('delta_raw', 'telemetry_corrections') }}
)
select
    v.message_id,
    v.device_id,
    v.site_id,
    v.sequence_number,
    coalesce(c.corrected_temperature_c, v.temperature_c) as temperature_c,
    coalesce(c.corrected_humidity_pct, v.humidity_pct) as humidity_pct,
    v.battery_pct,
    v.firmware,
    v.rssi_dbm,
    v.signal_quality_dbm,
    v.event_time,
    v.event_hour,
    v.ingested_from_hour,
    v.ingested_from_hour > v.event_hour as arrived_late,
    c.correction_reason
from versions as v
left join corrections as c on c.message_id = v.message_id
where v.version_rank = 1
