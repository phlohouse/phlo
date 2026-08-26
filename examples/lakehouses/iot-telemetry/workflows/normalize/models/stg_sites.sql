select
    site_id,
    site_name,
    region
from {{ source('iot_raw', 'site_directory') }}
