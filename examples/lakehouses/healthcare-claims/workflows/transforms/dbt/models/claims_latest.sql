-- Collapse the append-only version history to each claim's highest version.
-- Raw keeps every re-filed version for audit; only the latest reaches analytics.
with ranked as (
    select
        claim_version_key,
        claim_id,
        version,
        member_id,
        provider_id,
        service_date,
        procedure_codes,
        billed_amount,
        allowed_amount,
        paid_amount,
        row_number() over (partition by claim_id order by version desc, _phlo_ingested_at desc) as version_rank
    from {{ source('claims_raw', 'claims') }}
)
select
    claim_id,
    version,
    member_id,
    provider_id,
    cast(service_date as date) as service_date,
    procedure_codes,
    billed_amount,
    allowed_amount,
    paid_amount
from ranked
where version_rank = 1
