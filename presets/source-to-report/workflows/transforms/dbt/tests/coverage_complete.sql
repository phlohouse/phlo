-- Fail when a station or pollutant covers less than 95% of the year.
select station_id, pollutant, coverage
from {{ ref('station_coverage') }}
where coverage < 0.95
