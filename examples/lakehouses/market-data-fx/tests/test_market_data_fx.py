"""Fast deterministic contract tests for the market data and FX example."""

from __future__ import annotations

import json
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets

from scripts.generate_fixtures import (
    MARKET_CLOSURES,
    build_bars,
    build_corrections,
    build_fx,
    close_price,
    generate,
)
from scripts.replay_server import serve
from workflows.markets import schedules as markets_schedules
from workflows.markets.foreign_exchange.quality import (
    assert_fx_cross_tolerance,
    fx_cross_violations,
)
from workflows.markets.foreign_exchange.rates import fetch_rates
from workflows.markets.prices.equities import fetch_bars
from workflows.markets.prices.quality import (
    assert_calendar_coverage,
    assert_ohlc_relationships,
    assert_tick_precision,
)
from workflows.markets.reference_data import references  # noqa: F401 - registers assets
from workflows.schemas.market_data import EquitiesBarSchema, FxRateSchema


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


@pytest.fixture(scope="module")
def baseline_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    bars = pd.DataFrame([row for rows in build_bars().values() for row in rows])
    rates = pd.DataFrame([row for rows in build_fx().values() for row in rows])
    calendar = pd.read_csv(data_dir / "reference" / "trading_calendar.csv", dtype=str)
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    calendar["is_trading_day"] = calendar["is_trading_day"].str.lower() == "true"
    securities = pd.read_csv(data_dir / "reference" / "security_master.csv", dtype=str)
    return {"bars": bars, "rates": rates, "calendar": calendar, "securities": securities}


def _tree_hash(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def test_fixtures_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    summary_one = generate(first)
    summary_two = generate(second)
    assert summary_one == summary_two
    assert _tree_hash(first) == _tree_hash(second)


def test_replay_server_serves_paginated_bars_and_fx(data_dir: Path) -> None:
    server = serve(data_dir=data_dir, port=0)
    try:
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}/v1"

        bars = fetch_bars("2026-08-10", url=base_url)
        assert {row["symbol"] for row in bars} == {"US1", "US2", "DE1", "UK1"}
        assert all(str(row["bar_id"]).endswith("2026-08-10") for row in bars)

        rates = fetch_rates("2026-08-10", url=base_url)
        assert {row["pair"] for row in rates} == {"EURUSD", "GBPUSD", "EURGBP"}
    finally:
        server.shutdown()


def test_baseline_passes_all_price_and_fx_checks(baseline_frames: dict[str, pd.DataFrame]) -> None:
    bars = baseline_frames["bars"]
    assert len(bars) == 38
    EquitiesBarSchema.validate(bars)
    FxRateSchema.validate(baseline_frames["rates"])
    assert_ohlc_relationships(bars)
    assert_tick_precision(bars)
    assert_calendar_coverage(bars, baseline_frames["calendar"], baseline_frames["securities"])
    assert_fx_cross_tolerance(baseline_frames["rates"])
    de_dates = set(bars[bars.symbol == "DE1"].trade_date.str.slice(0, 10))
    uk_dates = set(bars[bars.symbol == "UK1"].trade_date.str.slice(0, 10))
    assert "2026-08-17" not in de_dates  # German market closure
    assert "2026-08-14" not in uk_dates  # UK market closure
    assert len(MARKET_CLOSURES) == 2


def test_labeled_failures_break_their_invariants(
    data_dir: Path, baseline_frames: dict[str, pd.DataFrame]
) -> None:
    failures = data_dir / "failures"

    ohlc = pd.DataFrame(json.loads((failures / "bars_ohlc_violation.json").read_text()))
    with pytest.raises(ValueError, match="OHLC"):
        assert_ohlc_relationships(ohlc)

    imprecise = pd.DataFrame(json.loads((failures / "bars_precision_violation.json").read_text()))
    with pytest.raises(ValueError, match="decimal places"):
        assert_tick_precision(imprecise)

    breach = pd.DataFrame(json.loads((failures / "fx_cross_breach.json").read_text()))
    with pytest.raises(ValueError, match="tolerance breached"):
        assert_fx_cross_tolerance(breach)

    # Warning mode surfaces the same evidence without raising.
    violations = fx_cross_violations(breach)
    assert len(violations) == 1
    assert violations[0]["rate_date"] == "2026-08-10"
    assert violations[0]["deviation_pct"] > 0.01

    # Missing observation: dropping UK1's 08-11 session breaks coverage.
    trimmed = baseline_frames["bars"]
    mask = (trimmed.symbol == "UK1") & (trimmed.trade_date.str.startswith("2026-08-11"))
    partial = trimmed[~mask]
    with pytest.raises(ValueError, match="UK1.*2026-08-11"):
        assert_calendar_coverage(
            partial, baseline_frames["calendar"], baseline_frames["securities"]
        )
    fixture_rows = json.loads((failures / "bars_missing_observation.json").read_text())
    assert sorted(row["symbol"] for row in fixture_rows) == ["DE1", "US1", "US2"]


def test_exact_metrics_follow_from_the_arithmetic_paths() -> None:
    assert close_price("US1", 0) == 100.00
    assert close_price("US1", 1) == 100.50
    assert close_price("DE1", 9) == round(80.00 - 0.40 * 9, 2) == 76.40
    us1_bars = [row for rows in build_bars().values() for row in rows if row["symbol"] == "US1"]
    returns = [
        round(float(us1_bars[i]["close_px"]) / float(us1_bars[i - 1]["close_px"]) - 1, 8)
        for i in range(1, len(us1_bars))
    ]
    assert returns[0] == 0.005
    assert max(returns) <= 0.01
    corrections = build_corrections()
    assert corrections[0]["corrected_close_px"] == close_price("US1", 7) - 0.50


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {
        "dlt_equities_bars",
        "dlt_equity_corrections",
        "dlt_fx_rates",
        "dlt_security_master",
        "dlt_trading_calendar",
        "dlt_portfolio_holdings",
    }
    equities = assets["dlt_equities_bars"]
    assert equities.metadata["write_mode"] == "merge"
    assert equities.metadata["primary_key"] == ["bar_id"]
    assert equities.metadata["owner"] == "markets-data-eng"
    assert equities.run.max_retries == 3
    assert equities.run.freshness_hours == (16, 20)
    assert assets["dlt_equity_corrections"].run.freshness_hours == (48, 54)
    assert assets["dlt_fx_rates"].metadata["owner"] == "treasury-ops"
    assert assets["dlt_portfolio_holdings"].run.freshness_hours == (720, 744)
    assert all(asset.checks[0].blocking for asset in assets.values())


def test_schedules_follow_trading_week_cadences() -> None:
    registered = (
        markets_schedules.weekday_market_ingestion_schedule,
        markets_schedules.reference_refresh_schedule,
        markets_schedules.analytics_rebuild_schedule,
        markets_schedules.weekly_reconciliation_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "30 5 * * 1-5",
        "0 3 * * 1-5",
        "45 5 * * 1-5",
        "0 6 * * 6",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )


def test_dbt_models_carry_expected_evidence() -> None:
    root = Path(__file__).resolve().parents[1] / "workflows" / "transforms" / "dbt" / "models"

    prices = (root / "prices/prices_normalized.sql").read_text(encoding="utf-8")
    assert "coalesce(c.corrected_close_px, b.close_px)" in prices
    assert "at_timezone(c.ts_utc, s.market_tz)" in prices
    assert "session_date_local" in prices
    assert "case when s.trading_ccy = 'USD' then 1.0 else f.rate end" in prices

    returns = (root / "prices/daily_returns.sql").read_text(encoding="utf-8")
    assert "lag(p.close_usd)" in returns
    assert "cal.is_trading_day" in returns

    volatility = (root / "prices/rolling_volatility.sql").read_text(encoding="utf-8")
    assert "stddev_samp(b.daily_return) * sqrt(252)" in volatility
    assert "b.rn between a.rn - 4 and a.rn" in volatility
    assert "where a.rn >= 5" in volatility

    drawdown = (root / "prices/drawdown.sql").read_text(encoding="utf-8")
    assert "max(close_usd) over (partition by symbol order by trade_date)" in drawdown

    cross = (root / "fx/fx_cross_check.sql").read_text(encoding="utf-8")
    assert "'breach'" in cross and "within_tolerance" in cross

    exposure = (root / "analytics/portfolio_exposure.sql").read_text(encoding="utf-8")
    assert "sum(position_value_usd) over (partition by portfolio)" in exposure
