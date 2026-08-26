-- Currency and timezone normalization over merged bars with corrections overlaid.
-- The session date is derived in each market's local timezone because the FX
-- rate that applies to a session is keyed by its local trading day.
with corrected as (
    select
        b.bar_id,
        b.symbol,
        b.market,
        b.trade_date,
        b.ts_utc,
        b.open_px,
        b.high_px,
        b.low_px,
        coalesce(c.corrected_close_px, b.close_px) as close_px_local_ccy,
        b.volume
    from {{ source('markets_raw', 'equities_bars') }} as b
    left join {{ source('markets_raw', 'equity_corrections') }} as c
        on c.bar_id = b.bar_id
)
select
    c.bar_id,
    c.symbol,
    c.market,
    s.trading_ccy,
    cast(c.trade_date as date) as trade_date,
    at_timezone(c.ts_utc, s.market_tz) as session_ts_local,
    cast(at_timezone(c.ts_utc, s.market_tz) as date) as session_date_local,
    c.open_px,
    c.high_px,
    c.low_px,
    c.close_px_local_ccy,
    round(
        c.close_px_local_ccy * case when s.trading_ccy = 'USD' then 1.0 else f.rate end,
        4
    ) as close_usd,
    case when s.trading_ccy = 'USD' then 1.0 else f.rate end as fx_to_usd,
    c.volume
from corrected as c
join {{ ref('stg_securities') }} as s on s.symbol = c.symbol
left join {{ source('markets_raw', 'fx_rates') }} as f
    on f.pair = s.trading_ccy || 'USD'
   and cast(f.rate_date as date) = cast(c.trade_date as date)
