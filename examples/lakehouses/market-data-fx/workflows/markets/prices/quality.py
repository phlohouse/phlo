"""Price-domain quality checks: OHLC relationships, tick precision, coverage.

All validators run on plain DataFrames so pytest screens generated fixtures and
operators can run them diagnostically against live tables.
"""

from __future__ import annotations

import pandas as pd

MAX_PRICE_DECIMALS = 4


def assert_ohlc_relationships(bars: pd.DataFrame) -> None:
    """High must dominate open/close/low; low must be their floor."""
    breaches = bars[
        (bars.high_px < bars[["open_px", "close_px"]].max(axis=1))
        | (bars.low_px > bars[["open_px", "close_px"]].min(axis=1))
        | (bars.high_px < bars.low_px)
    ]
    if not breaches.empty:
        offenders = breaches.bar_id.head(5).tolist()
        raise ValueError(f"OHLC relationships violated for bars: {offenders}")


def assert_tick_precision(bars: pd.DataFrame, max_decimals: int = MAX_PRICE_DECIMALS) -> None:
    """Prices must respect the venue tick size (decimal places budget)."""
    columns = ["open_px", "high_px", "low_px", "close_px"]
    for column in columns:
        scaled = bars[column] * 10**max_decimals
        imprecise = bars[(scaled - scaled.round()).abs() > 1e-6]
        if not imprecise.empty:
            offender = imprecise.iloc[0]
            raise ValueError(
                f"{column} exceeds {max_decimals} decimal places "
                f"for bar {offender.bar_id}: {offender[column]}"
            )


def assert_calendar_coverage(
    bars: pd.DataFrame, calendar: pd.DataFrame, securities: pd.DataFrame
) -> None:
    """Every symbol needs an observation on each of its market's trading days.

    Market closures are calendar-aware: a missing bar on a day flagged
    ``is_trading_day = false`` is expected, while a missing observation on a
    trading day is a failure naming the symbol and date.
    """
    observed = bars.copy()
    observed["date"] = pd.to_datetime(observed.trade_date, utc=True).dt.floor("D")
    window = (observed.date.min(), observed.date.max())
    trading = calendar[calendar.is_trading_day].copy()
    trading["date"] = pd.to_datetime(trading.calendar_date, utc=True).dt.floor("D")

    symbol_market = dict(zip(securities.symbol, securities.market, strict=True))
    missing: list[tuple[str, str]] = []
    for symbol, market in sorted(symbol_market.items()):
        expected_dates = trading[(trading.market == market) & trading.date.between(*window)].date
        seen_dates = set(observed[(observed.symbol == symbol) & (observed.market == market)].date)
        for date in sorted(set(expected_dates) - seen_dates):
            missing.append((symbol, date.date().isoformat()))
    if missing:
        raise ValueError(f"Missing observations on trading days: {missing[:5]}")
