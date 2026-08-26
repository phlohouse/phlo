-- Hourly request throughput per tenant, appended like the other hourly marts.
{{ config(
    materialized='incremental',
    incremental_strategy='append',
) }}
select
    toStartOfHour(occurred_at) as event_hour,
    tenant_id,
    count(*) as request_count
from {{ ref('stg_access_logs_dedup') }}
{% if is_incremental() %}
where event_hour > (select max(event_hour) from {{ this }})
{% endif %}
group by event_hour, tenant_id
