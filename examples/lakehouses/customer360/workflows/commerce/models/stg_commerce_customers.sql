select
    customer_id,
    email as observed_email,
    concat(
        split_part(split_part(lower(email), '@', 1), '+', 1),
        '@',
        split_part(lower(email), '@', 2)
    ) as canonical_email,
    full_name,
    segment,
    region,
    signup_date,
    updated_at
from {{ source('c360_raw', 'raw_customers') }}
