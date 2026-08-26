select
    eligibility_key,
    member_id,
    plan,
    payer,
    cast(effective_start as date) as effective_start,
    cast(effective_end as date) as effective_end
from {{ source('claims_raw', 'eligibility_periods') }}
