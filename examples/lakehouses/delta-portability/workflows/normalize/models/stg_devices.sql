select
    device_id,
    site_id,
    model,
    activated_at,
    decommissioned_at
from {{ source('delta_raw', 'device_registry') }}
