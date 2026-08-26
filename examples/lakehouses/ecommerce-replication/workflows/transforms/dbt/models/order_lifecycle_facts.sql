-- Order lifecycle facts: one row per order with line and payment aggregates.
-- Sling's Iceberg target is append-only for incremental mode (primary-key
-- merge unsupported upstream), so updated source rows arrive as additional
-- versions. Every stream is collapsed to its latest version by updated_at
-- before joining, which mirrors CDC read-time semantics.
with orders_latest as (
    select *
    from (
        select
            *,
            row_number() over (partition by order_id order by updated_at desc) as version
        from {{ source('commerce_raw', 'raw_orders') }}
    )
    where version = 1
),
line_stats as (
    select
        order_id,
        count(*) as line_count,
        sum(quantity) as unit_count,
        round(sum(line_amount), 2) as lines_total
    from (
        select
            *,
            row_number() over (
                partition by order_id, line_id order by updated_at desc
            ) as version
        from {{ source('commerce_raw', 'raw_order_lines') }}
    )
    where version = 1
    group by order_id
),
paid as (
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
    o.order_id,
    o.customer_id,
    o.status,
    o.currency,
    o.total_amount,
    coalesce(l.line_count, 0) as line_count,
    coalesce(l.unit_count, 0) as unit_count,
    coalesce(l.lines_total, 0.0) as lines_total,
    coalesce(p.paid_total, 0.0) as paid_total,
    o.ordered_at,
    o.updated_at,
    cast(o.ordered_at as date) as ordered_date
from orders_latest o
left join line_stats l on l.order_id = o.order_id
left join paid p on p.order_id = o.order_id
