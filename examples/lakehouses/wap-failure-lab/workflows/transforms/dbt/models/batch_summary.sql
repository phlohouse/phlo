{{ config(materialized='table') }}

-- Minimal downstream proof for the failure lab: this aggregate only ever sees
-- rows that reached main. A blocked or failed WAP run leaves it untouched;
-- a promoted run extends it. Bounded aggregation, no window frames (Trino
-- branch-CTAS safe).

select
    sensor_id,
    count(*) as batch_count,
    count(distinct batch_id) as distinct_batches,
    min(recorded_at) as earliest_recording,
    max(recorded_at) as latest_recording
from {{ source('raw', 'sensor_batches') }}
group by sensor_id
