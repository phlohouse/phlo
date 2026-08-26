select account_id,
    min(case when event_type = 'signup' then occurred_at end) as signup_at,
    min(case when event_type = 'project_created' then occurred_at end) as activated_at,
    max(case when event_type = 'project_created' then 1 else 0 end) = 1 as is_activated
from {{ ref('flattened_events') }}
group by 1
