select
    calendar_key,
    market,
    calendar_date,
    is_trading_day
from {{ source('markets_raw', 'trading_calendar') }}
