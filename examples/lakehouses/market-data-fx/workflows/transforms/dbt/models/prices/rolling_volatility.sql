-- Annualized realized volatility over the trailing five sessions.
--
-- Implemented as a bounded self-join rather than a sliding window frame:
-- each session aggregates exactly its own and the four preceding returns,
-- and rows are emitted only once the window is full so every value is defined.
with numbered as (
    select
        symbol,
        trade_date,
        daily_return,
        row_number() over (partition by symbol order by trade_date) as rn
    from {{ ref('daily_returns') }}
)
select
    a.symbol || '|' || cast(a.trade_date as varchar) as vol_key,
    a.symbol,
    a.trade_date,
    round(stddev_samp(b.daily_return) * sqrt(252), 6) as realized_vol_5d
from numbered as a
join numbered as b
    on b.symbol = a.symbol
   and b.rn between a.rn - 4 and a.rn
where a.rn >= 5
group by a.symbol, a.trade_date
