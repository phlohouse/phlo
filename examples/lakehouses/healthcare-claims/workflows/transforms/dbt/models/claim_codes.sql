-- Code/array normalization: one row per claim and procedure code.
with exploded as (
    select
        claim_id,
        version,
        procedure_codes
    from {{ ref('claims_latest') }}
)
select distinct
    claim_id || '|' || trim(code) as claim_code_key,
    claim_id,
    upper(trim(code)) as procedure_code
from exploded
cross join unnest(split(procedure_codes, '|')) as t(code)
