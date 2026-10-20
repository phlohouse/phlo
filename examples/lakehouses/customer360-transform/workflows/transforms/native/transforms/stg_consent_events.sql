-- translated from dbt model `stg_consent_events` (../../marketing/models/stg_consent_events.sql)
-- @not-null event_key

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
from raw.consent_events
