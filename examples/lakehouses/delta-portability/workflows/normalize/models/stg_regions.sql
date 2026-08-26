-- Regions lookup replicated from PostgreSQL by the Sling stream and merged
-- into the Delta table raw.delta_regions.
select
    region_code,
    region_name,
    country,
    updated_at
from {{ source('delta_raw', 'delta_regions') }}
