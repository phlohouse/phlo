-- Canonical shipment state: one row per shipment, decided by event time.
--
-- Ordering contract (mirrored by workflows/control_tower/transforms/state_logic.py):
--   1. The state of the event with the greatest event_time wins.
--   2. Equal timestamps break by severity: exception > delivered > the rest.
--   3. A delivered event that happens after an exception clears it; a
--      contradiction (both states present) stays visible in
--      contradiction_count so carrier data quality is auditable.
--
-- Bounded self-joins instead of window-frame aggregates, which misbehave under
-- branch CTAS on this stack.

with ranked_events as (

    select
        events.shipment_id,
        events.carrier,
        events.event_id,
        events.event_type,
        events.event_time,
        events.location,
        case events.event_type
            when 'exception' then 3
            when 'delivered' then 2
            else 1
        end as severity_rank
    from {{ source('logistics_raw', 'carrier_events') }} as events
)

, latest_times as (

    select
        shipment_id,
        max(event_time) as latest_event_time
    from ranked_events
    group by shipment_id

),

latest_candidates as (

    select ranked.*
    from ranked_events as ranked
    inner join latest_times
        on ranked.shipment_id = latest_times.shipment_id
        and ranked.event_time = latest_times.latest_event_time

),

max_severity as (

    select
        shipment_id,
        max(severity_rank) as top_severity
    from latest_candidates
    group by shipment_id

),

winners as (

    select candidate.*
    from latest_candidates as candidate
    inner join max_severity
        on candidate.shipment_id = max_severity.shipment_id
        and candidate.severity_rank = max_severity.top_severity

),

contradictions as (

    select shipment_id
    from ranked_events
    where event_type in ('delivered', 'exception')
    group by shipment_id
    having count(distinct event_type) = 2

)

select
    winners.shipment_id,
    winners.carrier,
    winners.event_id as canonical_event_id,
    winners.event_type as canonical_state,
    winners.event_time as state_as_of,
    winners.location,
    cast(case when contradictions.shipment_id is not null then 1 else 0 end as bigint)
        as contradiction_count
from winners
left join contradictions
    on winners.shipment_id = contradictions.shipment_id
