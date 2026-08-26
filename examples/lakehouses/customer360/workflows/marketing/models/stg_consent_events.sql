select
    event_key,
    email as observed_email,
    concat(
        split_part(split_part(lower(email), '@', 1), '+', 1),
        '@',
        split_part(lower(email), '@', 2)
    ) as canonical_email,
    consent_status,
    source,
    occurred_at
from {{ source('marketing_raw', 'raw_consent_events') }}
