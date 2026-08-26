{{ config(materialized='table', tags=['nightly']) }}

-- Invoice aging against the fixed snapshot horizon 2026-08-31.
--
-- CROSS-DOMAIN NOTE: this model joins the SALES domain's raw deals table.
-- dbt has no cross-project ref(), so sales_deals is declared here as a local
-- raw-table source whose meta.phlo_asset_key points at the sales domain's
-- ingestion asset. Resolving that reference requires BOTH manifests to be
-- active at once, which the single-active-project runtime does not support
-- (see FEDERATION_FINDINGS.md); under sales activation this model is inert.
with invoices as (

    select *
    from {{ source('finance_raw', 'finance_invoices') }}

),

deals as (

    select deal_id, account_name
    from {{ source('sales_raw', 'sales_deals') }}

),

aged as (

    select
        i.invoice_id,
        i.deal_id,
        d.account_name,
        i.customer,
        i.amount_usd,
        case
            when i.paid_on is not null then 'paid'
            when date_diff('day', cast(i.due_on as date), date '2026-08-31') <= 0 then 'current'
            when date_diff('day', cast(i.due_on as date), date '2026-08-31') <= 30 then '1-30'
            when date_diff('day', cast(i.due_on as date), date '2026-08-31') <= 60 then '31-60'
            else '60+'
        end as aging_bucket,
        coalesce(
            date_diff('day', cast(i.due_on as date), cast(i.paid_on as date)), 0
        ) as days_paid_late

    from invoices as i
    inner join deals as d
        on i.deal_id = d.deal_id

)

select
    aging_bucket,
    count(*) as invoice_count,
    sum(amount_usd) as outstanding_value_usd,
    max(days_paid_late) as worst_days_paid_late

from aged

group by aging_bucket
order by aging_bucket
