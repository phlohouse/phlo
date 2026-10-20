select
    ticket_id,
    email as observed_email,
    concat(
        split_part(split_part(lower(email), '@', 1), '+', 1),
        '@',
        split_part(lower(email), '@', 2)
    ) as canonical_email,
    subject,
    created_at,
    resolved_at
from {{ source('support_raw', 'raw_tickets') }}
