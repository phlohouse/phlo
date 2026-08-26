select
    email as observed_email,
    concat(
        split_part(split_part(lower(email), '@', 1), '+', 1),
        '@',
        split_part(lower(email), '@', 2)
    ) as canonical_email,
    contact_name,
    list_segment,
    captured_at
from {{ source('marketing_raw', 'raw_contacts') }}
