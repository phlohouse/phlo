{{ config(materialized='table', tags=['nightly']) }}

-- Pipeline rollup over the CRM snapshot merged by dlt_sales_deals.
with deals as (

    select *
    from {{ source('sales_raw', 'sales_deals') }}

)

select
    stage,
    count(*) as deal_count,
    sum(amount_usd) as pipeline_value_usd,
    min(opened_on) as earliest_open,
    max(stage_updated_at) as latest_stage_change

from deals

group by stage
order by stage
