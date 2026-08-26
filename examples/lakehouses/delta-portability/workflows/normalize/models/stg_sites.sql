select
    site_id,
    site_name,
    region
from {{ source('delta_raw', 'site_directory') }}
