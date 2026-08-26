with cohorts as (
    select
        account_id,
        cast(min(case when event_type = 'signup' then occurred_at end) as date) as cohort_date
    from {{ ref('flattened_events') }}
    group by 1
), activity as (
    select distinct account_id, cast(occurred_at as date) as activity_date from {{ ref('flattened_events') }}
)
select c.cohort_date, date_diff('day', c.cohort_date, a.activity_date) as days_since_signup,
    count(distinct account_id) as retained_accounts
from cohorts c join activity a using (account_id)
group by 1, 2
