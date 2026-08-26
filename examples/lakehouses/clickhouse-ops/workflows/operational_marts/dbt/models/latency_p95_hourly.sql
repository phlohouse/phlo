-- Hourly p95 request latency.
--
-- quantileExact(0.95) is chosen over the approximate quantile() so the
-- fixture result is deterministic and exact: with 21 samples per hour the
-- nearest rank ceil(0.95*21) = 20 (one-indexed) is also the interpolated
-- position, so exact and interpolated quantiles agree on this data. The
-- modest query-latency target for this mart is sub-second on one hour of
-- fixture data; ClickHouse serves it directly from the store role without a
-- Trino round trip.
{{ config(
    materialized='incremental',
    incremental_strategy='append',
) }}
select
    toStartOfHour(occurred_at) as event_hour,
    quantileExact(0.95)(duration_ms) as p95_duration_ms,
    max(duration_ms) as max_duration_ms
from {{ ref('stg_access_logs_dedup') }}
{% if is_incremental() %}
where event_hour > (select max(event_hour) from {{ this }})
{% endif %}
group by event_hour
