select store_id, store_name, region, format, timezone, cast(open_date as date) as open_date
from {{ source('retail_raw', 'raw_stores') }}
