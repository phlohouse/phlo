-- Annual rollup: station-year climate totals reconciled across grains, then
-- joined to regional demographics. precip_delta compares the sum of monthly
-- aggregates against the direct annual sum and must be zero - the fixture
-- arithmetic guarantees both paths aggregate identical rows.
with monthly as (
    select
        station_id,
        obs_month,
        sum(precip_mm) as precip_mm_month
    from {{ ref('stg_observations') }}
    group by
        station_id,
        obs_month
),
station_year as (
    select
        station_id,
        cast(date_trunc('year', obs_month) as date) as census_year,
        sum(precip_mm_month) as precip_mm_via_months,
        avg(precip_mm_month) as avg_precip_mm_month
    from monthly
    group by
        station_id,
        cast(date_trunc('year', obs_month) as date)
),
direct_annual as (
    select
        station_id,
        cast(date_trunc('year', observed_at) as date) as census_year,
        sum(precip_mm) as precip_mm_direct
    from {{ ref('stg_observations') }}
    group by
        station_id,
        cast(date_trunc('year', observed_at) as date)
),
reconciled as (
    select
        s.station_id,
        s.census_year,
        s.precip_mm_via_months,
        d.precip_mm_direct,
        s.precip_mm_via_months - d.precip_mm_direct as precip_delta
    from station_year s
    inner join direct_annual d
        on d.station_id = s.station_id
        and d.census_year = s.census_year
)
select
    r.station_id,
    p.place_name,
    p.region,
    r.census_year,
    r.precip_mm_via_months,
    r.precip_mm_direct,
    r.precip_delta,
    d.population as region_population,
    d.median_age as region_median_age
from reconciled r
inner join {{ ref('places') }} p
    on p.place_id = r.station_id
inner join {{ source('public_raw', 'region_demographics') }} d
    on d.region = p.region
    and d.year = year(r.census_year)
