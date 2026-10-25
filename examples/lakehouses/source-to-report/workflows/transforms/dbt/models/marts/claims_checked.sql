-- Every published figure against the measurement it claims to describe.
-- A claim with no measurement is `missing`, never a silent `match`.
{{ config(materialized='table') }}

select
    c.claim_id,
    cast(c.month || '-01' as date) as month,
    c.station_id,
    c.pollutant,
    c.value as published_value,
    m.mean_value as measured_value,
    round(c.value - m.mean_value, 2) as difference,
    case
        when m.mean_value is null then 'missing'
        when abs(c.value - m.mean_value) <= {{ var('claim_tolerance') }} then 'match'
        else 'differs'
    end as verdict,
    c.source_path,
    c.line,
    c.sentence
from {{ ref('stg_claims') }} c
left join {{ ref('monthly_station') }} m
    on cast(c.month || '-01' as date) = m.month
   and c.station_id = m.station_id
   and c.pollutant = m.pollutant
