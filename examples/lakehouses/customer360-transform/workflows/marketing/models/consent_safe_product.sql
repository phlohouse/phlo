-- Consent-gated publication of the current customer dimension. Every current
-- identity row is evaluated against its current consent state; rows surface
-- as contactable only when that state is granted, and every suppressed row
-- carries the reason it was withheld (revoked or never consented).
select
    d.canonical_email,
    case when cc.consent_status = 'granted' then true else false end as is_exposed,
    case
        when cc.consent_status is null then 'no consent record'
        when cc.consent_status = 'revoked' then 'consent revoked'
    end as suppression_reason,
    cc.consent_status,
    cc.consent_as_of
from {{ ref('customer_dimension') }} d

left join {{ ref('consent_current') }} cc
    on d.canonical_email = cc.canonical_email

where d.current_flag
