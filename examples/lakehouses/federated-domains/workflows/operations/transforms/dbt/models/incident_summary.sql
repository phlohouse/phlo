{{ config(materialized='table', tags=['nightly']) }}

-- Service x severity reliability rollup over incidents merged by
-- dlt_operations_incidents.
with incidents as (

    select *
    from {{ source('operations_raw', 'operations_incidents') }}

)

select
    service,
    severity,
    count(*) as incident_count,
    sum(case when resolved_at is null then 1 else 0 end) as open_incidents,
    avg(resolution_minutes) as mean_resolution_minutes

from incidents

group by service, severity
order by service, severity
