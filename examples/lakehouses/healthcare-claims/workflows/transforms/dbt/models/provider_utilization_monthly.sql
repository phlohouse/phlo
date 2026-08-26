-- Curated utilization mart: aggregated to provider and month. The privacy
-- contract forbids direct member identifiers in curated outputs.
select
    p.provider_id || '|' || substr(cast(month_start as varchar), 1, 7) as utilization_key,
    p.provider_id,
    s.provider_name,
    s.network_status,
    month_start as service_month,
    count(*) as claim_count,
    round(sum(p.billed_amount), 2) as total_billed,
    round(sum(p.allowed_amount), 2) as total_allowed,
    round(sum(p.paid_amount), 2) as total_paid
from (
    select
        provider_id,
        date_trunc('month', cast(service_date as timestamp)) as month_start,
        billed_amount,
        allowed_amount,
        paid_amount
    from {{ ref('valid_claims') }}
) as p
join {{ ref('stg_providers') }} as s on s.provider_id = p.provider_id
group by p.provider_id, s.provider_name, s.network_status, month_start
