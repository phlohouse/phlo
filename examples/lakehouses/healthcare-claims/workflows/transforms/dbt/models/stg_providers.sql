select
    provider_id,
    trim(name) as provider_name,
    lower(specialty) as specialty,
    npi,
    network_status
from {{ source('claims_raw', 'providers') }}
