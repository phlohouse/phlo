-- Support engagement per canonical identity, joined onto the current
-- dimension row. Aggregates stay bounded: a self-contained group-by over the
-- current slice only, no unbounded window frames.
select
    d.canonical_email,
    count(t.ticket_id) as ticket_count,
    sum(case when t.resolved_at is null then 1 else 0 end) as open_count,
    max(t.created_at) as latest_ticket_at
from {{ ref('customer_dimension') }} d

left join {{ ref('stg_support_tickets') }} t
    on d.canonical_email = t.canonical_email

where d.current_flag

group by d.canonical_email
