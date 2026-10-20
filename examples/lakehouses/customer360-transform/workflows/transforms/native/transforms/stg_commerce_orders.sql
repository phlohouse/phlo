-- translated from dbt model `stg_commerce_orders` (../../commerce/models/stg_commerce_orders.sql)

select
    order_id,
    email as observed_email,
    concat(
        split_part(split_part(lower(email), '@', 1), '+', 1),
        '@',
        split_part(lower(email), '@', 2)
    ) as canonical_email,
    status,
    currency,
    total_amount,
    ordered_at,
    updated_at
from raw.c360_orders
