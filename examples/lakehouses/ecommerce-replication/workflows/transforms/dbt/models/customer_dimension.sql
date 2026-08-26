-- Current-state customer dimension from the accumulating snapshot table.
-- Snapshot mode appends one row per customer per run; the newest source
-- version wins by updated_at; the DLT-path ingestion lineage columns are not
-- present on Sling-replicated tables, so they cannot act as tie-breakers.
with ranked as (
    select
        *,
        row_number() over (
            partition by customer_id
            order by updated_at desc
        ) as recency
    from {{ source('commerce_raw', 'raw_customers') }}
)
select
    customer_id,
    email,
    full_name,
    segment,
    region,
    signup_date,
    updated_at
from ranked
where recency = 1
