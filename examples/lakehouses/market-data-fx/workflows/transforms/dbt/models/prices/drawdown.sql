-- Drawdown from the running peak of the USD-normalized close.
with priced as (
    select
        symbol,
        trade_date,
        close_usd,
        max(close_usd) over (partition by symbol order by trade_date) as peak_usd
    from {{ ref('prices_normalized') }}
)
select
    symbol || '|' || cast(trade_date as varchar) as drawdown_key,
    symbol,
    trade_date,
    close_usd,
    peak_usd,
    round((close_usd - peak_usd) / peak_usd, 6) as drawdown_pct
from priced
