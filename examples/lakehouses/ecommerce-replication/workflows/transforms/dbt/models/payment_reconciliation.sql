-- Payment reconciliation evidence: flags every order whose payments diverge
-- from its total beyond tolerance, or whose delivered status is unpaid.
-- Payments are deduplicated to the latest version per payment_id because the
-- incremental stream appends updated rows (Sling Iceberg target is
-- append-only).
with paid as (
    select
        order_id,
        round(sum(amount), 2) as paid_total
    from (
        select
            *,
            row_number() over (partition by payment_id order by updated_at desc) as version
        from {{ source('commerce_raw', 'raw_payments') }}
    )
    where version = 1
    group by order_id
)
select
    f.order_id,
    f.status,
    f.total_amount,
    coalesce(p.paid_total, 0.0) as paid_total,
    round(f.total_amount - coalesce(p.paid_total, 0.0), 2) as variance,
    case
        when abs(f.total_amount - coalesce(p.paid_total, 0.0)) > 0.01 then 'over_or_under_paid'
        when f.status = 'delivered' and coalesce(p.paid_total, 0.0) + 0.01 < f.total_amount then 'delivered_unpaid'
        else 'reconciled'
    end as reconciliation_status
from {{ ref('order_lifecycle_facts') }} f
left join paid p on p.order_id = f.order_id
