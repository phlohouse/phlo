-- Read-time deduplication for access logs.
--
-- Mirrors the platform-event collapse: one row per request_id with the
-- latest occurred_at winning. The fixture logs are clean, but keeping the
-- same read-time contract means a replayed hour can never double-count.
{{ config(materialized='view') }}
with versions as (
    select
        l.*,
        row_number() over (
            partition by l.request_id
            order by l.occurred_at desc, l._phlo_ingested_at desc
        ) as version_rank
    from {{ source('ch_raw', 'access_logs') }} as l
)
select
    request_id,
    tenant_id,
    path,
    status_code,
    duration_ms,
    occurred_at
from versions
where version_rank = 1
