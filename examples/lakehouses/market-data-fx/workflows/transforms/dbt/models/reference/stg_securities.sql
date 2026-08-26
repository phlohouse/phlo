select
    symbol,
    name,
    market,
    trading_ccy,
    market_tz
from {{ source('markets_raw', 'security_master') }}
