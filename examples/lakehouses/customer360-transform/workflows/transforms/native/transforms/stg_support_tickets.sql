-- translated from dbt model `stg_support_tickets` (../../support/models/stg_support_tickets.sql)
-- Tickets with canonical emails resolved; open tickets keep a null resolved_at.
-- @not-null ticket_id
-- @not-null canonical_email

-- Support tickets with canonical emails resolved; open tickets keep a null resolved_at.
select
    ticket_id,
    email as observed_email,
    concat(
        split_part(split_part(lower(email), '@', 1), '+', 1),
        '@',
        split_part(lower(email), '@', 2)
    ) as canonical_email,
    subject,
    created_at,
    resolved_at
from raw.support_tickets
