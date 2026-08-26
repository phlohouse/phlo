-- Read-time deduplication for platform events.
--
-- Raw is append-only and the delivery layer replays micro-batches verbatim,
-- so event_id values accumulate duplicate versions. This model keeps exactly
-- one version per event_id (latest occurred_at wins, ingestion timestamp as
-- tie-breaker) so every downstream mart is replay-idempotent.
{{ config(materialized='view') }}
with versions as (
    select
        e.*,
        row_number() over (
            partition by e.event_id
            order by e.occurred_at desc, e._phlo_ingested_at desc
        ) as version_rank
    from {{ source('ch_raw', 'platform_events') }} as e
)
select
    event_id,
    tenant_id,
    event_type,
    occurred_at,
    latency_ms
from versions
where version_rank = 1
