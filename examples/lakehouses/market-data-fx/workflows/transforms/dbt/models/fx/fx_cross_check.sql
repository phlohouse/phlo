-- Reconciliation surface for the EURGBP cross: quoted versus implied.
-- Breaches set status='breach' for warning dashboards; blocking publication
-- decisions stay separated in the quality modules and ingestion contracts.
with pivoted as (
    select
        cast(rate_date as date) as rate_date,
        max(case when pair = 'EURUSD' then rate end) as eur_usd,
        max(case when pair = 'GBPUSD' then rate end) as gbp_usd,
        max(case when pair = 'EURGBP' then rate end) as eur_gbp
    from {{ source('markets_raw', 'fx_rates') }}
    group by 1
)
select
    cast(rate_date as varchar) as check_key,
    rate_date,
    eur_usd,
    gbp_usd,
    eur_gbp,
    round(eur_usd / gbp_usd, 4) as implied_eur_gbp,
    round(abs(eur_gbp - eur_usd / gbp_usd) / (eur_usd / gbp_usd), 6) as deviation_pct,
    case
        when abs(eur_gbp - eur_usd / gbp_usd) / (eur_usd / gbp_usd) <= 0.001
            then 'within_tolerance'
        else 'breach'
    end as status
from pivoted
