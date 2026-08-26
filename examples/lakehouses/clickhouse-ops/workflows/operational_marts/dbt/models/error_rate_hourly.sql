-- Hourly error rate per tenant, appended one hour at a time.
--
-- The hourly marts append because each refresh covers exactly the newest
-- operating hour; appended rows are immutable once written. Reconciliation
-- against tenant_usage_daily (sum of hours == daily total) proves no refresh
-- dropped or duplicated an hour.
{{ config(
    materialized='incremental',
    incremental_strategy='append',
) }}
select
    toStartOfHour(occurred_at) as event_hour,
    tenant_id,
    count(*) as request_count,
    countIf(status_code >= 500) as error_count
from {{ ref('stg_access_logs_dedup') }}
{% if is_incremental() %}
where event_hour > (select max(event_hour) from {{ this }})
{% endif %}
group by event_hour, tenant_id
