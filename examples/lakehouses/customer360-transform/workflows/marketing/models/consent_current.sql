-- Current consent state per canonical identity: latest occurred_at wins.
-- Ingestion guarantees no two events share an exact timestamp per email
-- (blocking quality check), so this pick is always decidable.
with ranked as (
    select
        canonical_email,
        consent_status,
        source,
        occurred_at,
        row_number() over (
            partition by canonical_email
            order by occurred_at desc
        ) as recency_rank
    from {{ ref('stg_consent_events') }}
)

select
    canonical_email,
    consent_status,
    source,
    occurred_at as consent_as_of
from ranked
where recency_rank = 1
