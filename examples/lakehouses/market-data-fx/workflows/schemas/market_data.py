"""Contracts for market data, FX rates, and reference tables.

Temporal fields are typed natively because DLT normalizes ISO-8601 strings to
timestamps during staging. Numeric fields carry strict bounds; tick-level
precision and cross-rate tolerances are validated in the quality modules.
"""

from __future__ import annotations

from datetime import datetime

import pandera.pandas as pa
from pandera.typing import Series

PRICE_MAX = 1_000_000.0
RATE_MIN = 0.10
RATE_MAX = 500.0


class EquitiesBarSchema(pa.DataFrameModel):
    """One OHLCV session bar; ``bar_id`` merges corrections and replays."""

    bar_id: Series[str]
    symbol: Series[str] = pa.Field(str_matches=r"^[A-Z0-9]{3}$")
    market: Series[str] = pa.Field(isin=["us", "de", "uk"])
    trade_date: Series[datetime]
    ts_utc: Series[datetime]
    open_px: Series[float] = pa.Field(gt=0.0, le=PRICE_MAX)
    high_px: Series[float] = pa.Field(gt=0.0, le=PRICE_MAX)
    low_px: Series[float] = pa.Field(gt=0.0, le=PRICE_MAX)
    close_px: Series[float] = pa.Field(gt=0.0, le=PRICE_MAX)
    volume: Series[int] = pa.Field(ge=0)

    class Config:
        strict = False
        coerce = True


class EquityCorrectionSchema(pa.DataFrameModel):
    """Late print corrections amend an existing bar by ``bar_id``."""

    bar_id: Series[str]
    corrected_close_px: Series[float] = pa.Field(gt=0.0, le=PRICE_MAX)
    correction_reason: Series[str]
    corrected_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


class FxRateSchema(pa.DataFrameModel):
    """Daily FX observation; strict numeric bounds gate ingestion."""

    rate_id: Series[str]
    pair: Series[str] = pa.Field(str_matches=r"^[A-Z]{6}$")
    rate_date: Series[datetime]
    rate: Series[float] = pa.Field(ge=RATE_MIN, le=RATE_MAX)

    class Config:
        strict = False
        coerce = True


class SecurityMasterSchema(pa.DataFrameModel):
    """Instrument reference merged wholesale from the vendor file."""

    symbol: Series[str] = pa.Field(unique=True)
    name: Series[str]
    market: Series[str]
    trading_ccy: Series[str] = pa.Field(isin=["USD", "EUR", "GBP"])
    market_tz: Series[str]

    class Config:
        strict = False
        coerce = True


class CalendarEntrySchema(pa.DataFrameModel):
    """Trading-calendar flag per market and date."""

    calendar_key: Series[str] = pa.Field(unique=True)
    market: Series[str]
    calendar_date: Series[datetime]
    is_trading_day: Series[bool]

    class Config:
        strict = False
        coerce = True


class PortfolioHoldingSchema(pa.DataFrameModel):
    """Static portfolio position used for exposure analytics."""

    holding_key: Series[str] = pa.Field(unique=True)
    portfolio: Series[str]
    symbol: Series[str]
    quantity: Series[int] = pa.Field(ge=1)

    class Config:
        strict = False
        coerce = True
