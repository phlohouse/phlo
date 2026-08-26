-- Day-over-day returns sequenced along each market's trading calendar, so a
-- holiday closure never produces a synthetic zero return.
with sessions as (
    select
        p.symbol,
        p.trade_date,
        p.close_usd,
        lag(p.close_usd) over (partition by p.symbol order by p.trade_date) as prev_close_usd
    from {{ ref('prices_normalized') }} as p
    join {{ ref('stg_calendar') }} as cal
        on cal.market = p.market
       and cast(cal.calendar_date as date) = p.trade_date
       and cal.is_trading_day
)
select
    symbol || '|' || cast(trade_date as varchar) as return_key,
    symbol,
    trade_date,
    prev_close_usd,
    close_usd,
    round(close_usd / prev_close_usd - 1, 8) as daily_return
from sessions
where prev_close_usd is not null
