-- Subject-oriented places: civic registry joined to flattened GeoJSON
-- metadata, with names normalized for research use.
with registry as (
    select
        place_id,
        upper(trim(name)) as place_name,
        trim(lower(region)) as region,
        lat,
        lon,
        population_year,
        population,
        registry_date
    from {{ source('public_raw', 'places_registry') }}
),
geo as (
    select
        place_id,
        centroid_lat,
        centroid_lon,
        prop_region_code,
        prop_elevation_m,
        prop_classification
    from {{ source('public_raw', 'places_geo') }}
)
select
    r.place_id,
    r.place_name,
    r.region,
    r.lat,
    r.lon,
    g.centroid_lat,
    g.centroid_lon,
    g.prop_region_code,
    g.prop_elevation_m,
    g.prop_classification,
    r.population_year,
    r.population,
    r.registry_date
from registry r
inner join geo g
    on g.place_id = r.place_id
