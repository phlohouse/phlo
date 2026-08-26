-- Portfolio exposure weights from static positions valued at latest USD closes.
with latest_prices as (
    select
        symbol,
        close_usd,
        row_number() over (partition by symbol order by trade_date desc) as recency
    from {{ ref('prices_normalized') }}
),
positions as (
    select
        h.portfolio,
        h.symbol,
        h.quantity,
        p.close_usd,
        h.quantity * p.close_usd as position_value_usd
    from {{ source('markets_raw', 'portfolio_holdings') }} as h
    join latest_prices as p
        on p.recency = 1
       and p.symbol = h.symbol
)
select
    portfolio || '|' || symbol as exposure_key,
    portfolio,
    symbol,
    quantity,
    close_usd as price_usd_latest,
    position_value_usd,
    round(position_value_usd / sum(position_value_usd) over (partition by portfolio), 6) as weight
from positions
