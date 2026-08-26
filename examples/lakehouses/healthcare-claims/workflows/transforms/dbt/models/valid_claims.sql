-- Temporal eligibility join: a claim is valid when its service date falls in
-- one of its member's coverage periods. Claims without coverage stay out of
-- downstream marts; operations review them through the quality diagnostics.
with joined as (
    select
        c.claim_id,
        c.member_id,
        c.provider_id,
        c.service_date,
        c.billed_amount,
        c.allowed_amount,
        c.paid_amount,
        e.plan,
        e.eligibility_key,
        row_number() over (
            partition by c.claim_id
            order by e.effective_start desc
        ) as period_rank
    from {{ ref('claims_latest') }} as c
    join {{ ref('stg_eligibility_periods') }} as e
        on e.member_id = c.member_id
       and c.service_date between e.effective_start and e.effective_end
)
select
    claim_id,
    member_id,
    provider_id,
    service_date,
    plan,
    eligibility_key,
    billed_amount,
    allowed_amount,
    paid_amount
from joined
where period_rank = 1
