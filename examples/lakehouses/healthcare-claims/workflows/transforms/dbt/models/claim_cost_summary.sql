-- Curated cost summary by month: aggregate-only publication surface.
select
    'all' || '|' || substr(cast(month_start as varchar), 1, 7) as summary_key,
    month_start as service_month,
    count(*) as claim_count,
    round(avg(billed_amount), 2) as avg_billed,
    round(avg(allowed_amount), 2) as avg_allowed,
    round(avg(paid_amount), 2) as avg_paid,
    round(sum(paid_amount), 2) as total_paid
from (
    select
        date_trunc('month', cast(service_date as timestamp)) as month_start,
        billed_amount,
        allowed_amount,
        paid_amount
    from {{ ref('valid_claims') }}
) as normalized
group by month_start
