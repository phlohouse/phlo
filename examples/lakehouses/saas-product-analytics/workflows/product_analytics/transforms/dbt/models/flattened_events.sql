select
    event_id,
    cast(occurred_at as timestamp) as occurred_at,
    account_id,
    account_name,
    actor_id,
    actor_email,
    event_type,
    feature,
    experiment_variant,
    session_id,
    release
from {{ source('saas_raw', 'events') }}
