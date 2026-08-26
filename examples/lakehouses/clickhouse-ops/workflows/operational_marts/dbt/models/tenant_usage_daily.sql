-- Daily tenant usage: the replacing aggregate.
--
-- Where the hourly marts append immutable hour rows, this mart is rebuilt on
-- every hourly refresh and stored under ReplacingMergeTree keyed by
-- (usage_date, tenant_id): each rebuild writes a new version of the same key
-- and ClickHouse collapses to the latest at query time (FINAL). This is the
-- ClickHouse-native answer to aggregate replacement that Iceberg/Trino would
-- solve with WAP branch swap - a capability this data plane does not have.
{{ config(
    materialized='table',
    engine='ReplacingMergeTree()',
    order_by='(usage_date, tenant_id)',
) }}
select
    toDate(e.occurred_at) as usage_date,
    e.tenant_id,
    count(*) as event_count,
    coalesce(l.request_count, 0) as request_count,
    coalesce(l.error_count, 0) as error_count
from {{ ref('stg_platform_events_dedup') }} as e
left join (
    select
        tenant_id,
        toDate(occurred_at) as usage_date,
        count(*) as request_count,
        countIf(status_code >= 500) as error_count
    from {{ ref('stg_access_logs_dedup') }}
    group by tenant_id, usage_date
) as l
    on l.tenant_id = e.tenant_id and l.usage_date = toDate(e.occurred_at)
group by usage_date, e.tenant_id, l.request_count, l.error_count
