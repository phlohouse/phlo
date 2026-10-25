-- Fail when a published figure has no measurement to check it against.
select claim_id
from {{ ref('claims_checked') }}
where verdict = 'missing'
