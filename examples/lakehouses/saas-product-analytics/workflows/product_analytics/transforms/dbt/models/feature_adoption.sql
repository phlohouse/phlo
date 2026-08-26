select e.account_id, p.plan, e.feature, count(*) as feature_events,
    count(distinct e.actor_id) as adopting_users
from {{ ref('flattened_events') }} e
join {{ source('saas_raw', 'account_plans') }} p on e.account_id = p.account_id
where e.event_type = 'feature_used'
group by 1, 2, 3
