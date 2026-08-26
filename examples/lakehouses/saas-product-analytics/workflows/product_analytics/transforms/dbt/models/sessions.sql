with ordered as (
    select *, lag(occurred_at) over (partition by actor_id order by occurred_at, event_id) as prior_event_at
    from {{ ref('flattened_events') }}
), assigned as (
    select *, sum(case when prior_event_at is null or date_diff('minute', prior_event_at, occurred_at) > 30
        then 1 else 0 end) over (partition by actor_id order by occurred_at, event_id) as session_number
    from ordered
)
select account_id, actor_id, session_number, min(occurred_at) as session_started_at,
    max(occurred_at) as session_ended_at, count(*) as event_count
from assigned
group by 1, 2, 3
