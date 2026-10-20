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
from {{ source('c360_raw', 'raw_orders') }}
