"""Generate deterministic market data, FX, and reference fixtures.

Every byte the example consumes is derived from fixed arithmetic:

- ``api/bars-<date>.json``: paginated OHLCV bar payloads per trade date,
  shaped like the vendor REST feed the replay server serves.
- ``api/fx-<date>.json``: daily FX rate observations per trade date.
- ``reference/security_master.csv``, ``reference/trading_calendar.csv``,
  ``reference/portfolio_holdings.csv``: static reference tables.
- ``api/corrections.json``: late corrections merged onto previously ingested
  bars by ``bar_id``.
- ``failures/``: labeled invalid payloads; each breaks exactly one named
  invariant (OHLC relationships, tick precision, FX cross tolerance, missing
  observation).

Closes follow an arithmetic path ``base + step * index`` rounded to cents, so
returns, volatility, and drawdown are exactly reproducible.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

TRADE_DATES = [
    "2026-08-10",
    "2026-08-11",
    "2026-08-12",
    "2026-08-13",
    "2026-08-14",
    "2026-08-17",
    "2026-08-18",
    "2026-08-19",
    "2026-08-20",
    "2026-08-21",
]

# symbol -> name, market, trading currency, exchange timezone
SECURITIES: dict[str, dict[str, str]] = {
    "US1": {
        "name": "AAA Corp",
        "market": "us",
        "trading_ccy": "USD",
        "market_tz": "America/New_York",
    },
    "DE1": {
        "name": "BBB GmbH",
        "market": "de",
        "trading_ccy": "EUR",
        "market_tz": "Europe/Berlin",
    },
    "UK1": {
        "name": "CCC PLC",
        "market": "uk",
        "trading_ccy": "GBP",
        "market_tz": "Europe/London",
    },
    "US2": {
        "name": "DDD Inc",
        "market": "us",
        "trading_ccy": "USD",
        "market_tz": "America/New_York",
    },
}
CLOSE_STAMPS = {"us": "20:00:00Z", "de": "15:30:00Z", "uk": "15:30:00Z"}

# Arithmetic close paths: close(index) = base + step * index, rounded to cents.
PRICE_PATHS: dict[str, tuple[float, float]] = {
    "US1": (100.00, 0.50),
    "DE1": (80.00, -0.40),
    "UK1": (200.00, 1.00),
    "US2": (50.00, 0.25),
}

MARKET_CLOSURES = {("de", "2026-08-17"), ("uk", "2026-08-14")}

FX_BASE = {"EURUSD": 1.0800, "GBPUSD": 1.2500}
FX_STEP = {"EURUSD": 0.0010, "GBPUSD": -0.0005}

HOLDINGS: list[tuple[str, str, int]] = [
    ("core", "US1", 100),
    ("core", "DE1", 200),
    ("core", "UK1", 50),
    ("core", "US2", 400),
]

PAGE_SIZE = 2


def close_price(symbol: str, index: int) -> float:
    """Closing price for a symbol on the index-th session of the window."""
    base, step = PRICE_PATHS[symbol]
    return round(base + step * index, 2)


def sessions_for(market: str) -> list[int]:
    """Indices of sessions in the window where the market traded."""
    return [i for i, date in enumerate(TRADE_DATES) if (market, date) not in MARKET_CLOSURES]


def _bar(symbol: str, index: int) -> dict[str, object]:
    meta = SECURITIES[symbol]
    market = meta["market"]
    close = close_price(symbol, index)
    previous = (
        round(close - PRICE_PATHS[symbol][1] / 2, 2)
        if index == 0
        else close_price(symbol, index - 1)
    )
    open_ = previous
    high = round(max(open_, close) + 0.05, 2)
    low = round(min(open_, close) - 0.05, 2)
    date = TRADE_DATES[index]
    volume = 1000 + index * 37 + sorted(PRICE_PATHS).index(symbol) * 91
    return {
        "bar_id": f"{symbol}-{date}",
        "symbol": symbol,
        "market": market,
        "trade_date": f"{date}T00:00:00Z",
        "ts_utc": f"{date}T{CLOSE_STAMPS[market]}",
        "open_px": open_,
        "high_px": high,
        "low_px": low,
        "close_px": close,
        "volume": volume,
    }


def build_bars() -> dict[str, list[dict[str, object]]]:
    """Bars grouped by trade date, honoring market closures."""
    by_date: dict[str, list[dict[str, object]]] = {}
    for symbol, meta in SECURITIES.items():
        for index in sessions_for(meta["market"]):
            by_date.setdefault(TRADE_DATES[index], []).append(_bar(symbol, index))
    return dict(sorted(by_date.items()))


def paginate(rows: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Split rows into replay-server pages."""
    return [rows[i : i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]


def eur_usd_rate(index: int) -> float:
    return round(FX_BASE["EURUSD"] + FX_STEP["EURUSD"] * index, 4)


def gbp_usd_rate(index: int) -> float:
    return round(FX_BASE["GBPUSD"] + FX_STEP["GBPUSD"] * index, 4)


def build_fx() -> dict[str, list[dict[str, object]]]:
    """Daily FX observations; EURGBP quotes the implied cross exactly."""
    by_date: dict[str, list[dict[str, object]]] = {}
    for i, date in enumerate(TRADE_DATES):
        eur_usd = eur_usd_rate(i)
        gbp_usd = gbp_usd_rate(i)
        rows: list[dict[str, object]] = [
            {
                "rate_id": f"EURUSD-{date}",
                "pair": "EURUSD",
                "rate_date": f"{date}T00:00:00Z",
                "rate": eur_usd,
            },
            {
                "rate_id": f"GBPUSD-{date}",
                "pair": "GBPUSD",
                "rate_date": f"{date}T00:00:00Z",
                "rate": gbp_usd,
            },
            {
                "rate_id": f"EURGBP-{date}",
                "pair": "EURGBP",
                "rate_date": f"{date}T00:00:00Z",
                "rate": round(eur_usd / gbp_usd, 4),
            },
        ]
        by_date[date] = rows
    return by_date


def build_corrections() -> list[dict[str, object]]:
    """One late print correction merged onto an existing bar id."""
    index = TRADE_DATES.index("2026-08-19")
    corrected = round(close_price("US1", index) - 0.50, 2)
    return [
        {
            "bar_id": "US1-2026-08-19",
            "corrected_close_px": corrected,
            "correction_reason": "late_print",
            "corrected_at": "2026-08-21T09:00:00Z",
        }
    ]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_reference(data: Path) -> None:
    reference = data / "reference"
    reference.mkdir(parents=True)

    lines = ["symbol,name,market,trading_ccy,market_tz"]
    for symbol, meta in SECURITIES.items():
        lines.append(
            f"{symbol},{meta['name']},{meta['market']},{meta['trading_ccy']},{meta['market_tz']}"
        )
    (reference / "security_master.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    lines = ["market,calendar_date,is_trading_day"]
    for market in ("us", "de", "uk"):
        for date in TRADE_DATES:
            flag = "false" if (market, date) in MARKET_CLOSURES else "true"
            lines.append(f"{market},{date}T00:00:00Z,{flag}")
    (reference / "trading_calendar.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    lines = ["portfolio,symbol,quantity"]
    for portfolio, symbol, quantity in HOLDINGS:
        lines.append(f"{portfolio},{symbol},{quantity}")
    (reference / "portfolio_holdings.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_failures(data: Path) -> None:
    failures = data / "failures"
    failures.mkdir()

    violated = _bar("US1", 0)
    violated["bar_id"] = "fb-ohlc-1"
    violated["symbol"] = "FB1"
    violated["high_px"], violated["low_px"] = violated["low_px"], violated["high_px"]
    _write_json(failures / "bars_ohlc_violation.json", [violated])

    imprecise = _bar("US2", 0)
    imprecise["bar_id"] = "fb-tick-1"
    imprecise["close_px"] = 51.45678
    _write_json(failures / "bars_precision_violation.json", [imprecise])

    breached = []
    for row in build_fx()["2026-08-10"]:
        row = dict(row)
        if row["pair"] == "EURGBP":
            row["rate"] = round(float(row["rate"]) * 1.02, 4)  # 2% breach of the 10bp tolerance
        breached.append(row)
    _write_json(failures / "fx_cross_breach.json", breached)

    partial = [row for row in build_bars()["2026-08-11"] if row["symbol"] != "UK1"]
    _write_json(failures / "bars_missing_observation.json", partial)


def generate(data: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Regenerate every fixture under ``data`` and return summary counts."""
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)

    api = data / "api"
    bars_by_date = build_bars()
    for date, rows in bars_by_date.items():
        _write_json(api / f"bars-{date}.json", {"pages": paginate(rows)})
    fx_by_date = build_fx()
    for date, rows in fx_by_date.items():
        _write_json(api / f"fx-{date}.json", {"rates": rows})
    _write_json(api / "corrections.json", build_corrections())
    _write_reference(data)
    _write_failures(data)

    session_count = sum(len(sessions_for(meta["market"])) for meta in SECURITIES.values())
    return {
        "trade_dates": len(TRADE_DATES),
        "securities": len(SECURITIES),
        "bars": session_count,
        "fx_rates": len(fx_by_date) * 3,
        "corrections": len(build_corrections()),
        "holdings": len(HOLDINGS),
        "closures": len(MARKET_CLOSURES),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    print(generate(args.data_dir))
