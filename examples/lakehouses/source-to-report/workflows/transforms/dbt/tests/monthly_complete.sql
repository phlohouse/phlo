-- Fail when a month is missing a station or pollutant, or has a duplicate.
with actual as (
    select
        count(*) as rows,
        count(distinct month) as months
    from {{ ref('monthly_station') }}
)
select *
from actual
where rows <> months * {{ var('station_count') }} * {{ var('pollutant_count') }}
