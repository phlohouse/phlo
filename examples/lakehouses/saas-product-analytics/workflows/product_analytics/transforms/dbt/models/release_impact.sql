select release, feature, count(*) as feature_events, count(distinct account_id) as affected_accounts,
    count_if(experiment_variant is not null) as evolved_schema_events
from {{ ref('flattened_events') }}
where event_type = 'feature_used'
group by 1, 2
