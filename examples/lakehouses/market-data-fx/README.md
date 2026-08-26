# Market Data and FX lakehouse

A domain-first Phlo lakehouse that replays an equities API and an FX API into
Iceberg and builds currency-, timezone-, and calendar-aware analytics. It
exists to answer one question: do fixture portfolios produce exact metrics,
do valid market closures pass while missing observations fail, and can
corrected observations be merged without breaking history?

The project owns its uv environment, deterministic fixtures, replay server,
and workflow configuration. It does not depend on another example's runtime
state.

## What it exercises

| Area | Coverage |
|---|---|
| Sources | One replay API serving vendor-shaped paginated bars (`/v1/bars`) and daily FX rates (`/v1/fx`); live/replay modes switch via `MARKETS_API_URL` |
| Ingestion | Six `phlo.ingest.dlt` assets: bars and corrections merge by `bar_id`, FX by `rate_id`, security master, trading calendar, and portfolio holdings by natural keys |
| Domains | `markets/prices`, `markets/foreign_exchange`, `markets/reference_data` own their ingestion and quality; one central dbt project holds the SQL |
| Transforms | Currency normalization to USD, timezone normalization to local session dates, calendar-aware daily returns, 5-day annualized volatility, drawdown from running peak, cross-rate reconciliation, exposure weights |
| Quality | OHLC relationships, tick precision, calendar-aware observation coverage, FX cross tolerance with warning/blocking modes; strict numeric contracts at ingest |
| Scheduling | Weekday post-close ingestion, weekday reference refresh and analytics rebuilds, weekly full WAP reconciliation; all schedules stopped |
| Data plane | Iceberg tables in MinIO via Nessie catalog, Trino query engine, WAP branch promotion |

## Layout

```text
scripts/generate_fixtures.py   deterministic bars, FX, references, corrections, labeled failures
scripts/replay_server.py       vendor-shaped replay API (paginated bars, daily FX)
workflows/markets/prices/      bar + corrections ingestion; OHLC/precision/coverage checks
workflows/markets/foreign_exchange/    rate ingestion; cross-rate tolerance checks
workflows/markets/reference_data/      security master, trading calendar, holdings ingestion
workflows/schemas/             Pandera contracts
workflows/markets/schedules.py four stopped Dagster schedules
workflows/transforms/dbt/      central dbt project (prices/, fx/, reference/, analytics/)
tests/                         fast deterministic contract/failure tests
```

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run python scripts/replay_server.py --port 8092 &   # deterministic replay API
uv run pytest -q
uv run ruff check .
```

Start the platform, then materialize in dependency order, waiting for each WAP
report in `.phlo/wap-reports/` to reach `promoted`:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor

uv run phlo materialize dlt_security_master
uv run phlo materialize dlt_trading_calendar
uv run phlo materialize dlt_portfolio_holdings

uv run phlo backfill dlt_equities_bars \
  --partitions 2026-08-10,2026-08-11,2026-08-12,2026-08-13,2026-08-14,2026-08-17,2026-08-18,2026-08-19,2026-08-20,2026-08-21 \
  --parallel 5
uv run phlo backfill dlt_fx_rates --partitions 2026-08-10,... --parallel 5

uv run phlo materialize dlt_equity_corrections --partition 2026-08-10

uv run phlo materialize stg_securities --partition 2026-08-10
uv run phlo materialize stg_calendar --partition 2026-08-10
uv run phlo materialize prices_normalized --partition 2026-08-10
uv run phlo materialize daily_returns --partition 2026-08-10
uv run phlo materialize rolling_volatility --partition 2026-08-10
uv run phlo materialize drawdown --partition 2026-08-10
uv run phlo materialize fx_cross_check --partition 2026-08-10
uv run phlo materialize portfolio_exposure --partition 2026-08-10
```

Historical correction backfills re-run the analytics models after merging new
corrections; because every raw asset merges on a stable key, replays are
idempotent.

## Expected results (verified end to end)

The fixture window is ten weekdays (2026-08-10 through 2026-08-21), four
securities across three markets, with two closures: Germany on 08-17 and the
UK on 08-14.

- 38 bars land (each symbol misses only its market closure) plus 30 FX rates.
- Currency and timezone normalization is exact: `DE1-2026-08-10` closes at
  80.00 EUR and normalizes to `86.4` USD at EURUSD 1.0800; every session date
  derived in the market's local timezone matches its trade date.
- The late print correction moves `US1-2026-08-19` from 103.50 to `103.00`,
  so the next day's return reads `0.00970874` instead of `0.00485437`.
- Returns are calendar-aware: 34 rows - no synthetic zero-return rows across
  the German closure; US1's first return is exactly `0.005`.
- Volatility emits only full five-session windows (18 rows, zero nulls);
  US1's final annualized vol is `0.054493`.
- Drawdown covers all 38 sessions: declining DE1 bottoms at `-0.037042`
  while monotonically rising US1 never dips below `0.0`.
- All ten EURGBP observations reconcile within tolerance (`deviation_pct` 0);
  the cross-rate rule also rides as a blocking `quality_checks` entry on the
  FX ingestion asset, so a breaching batch fails closed.
- Exposure weights sum to exactly `1.000000` for the core portfolio.

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks one invariant,
proven by `tests/test_market_data_fx.py`:

- `bars_ohlc_violation.json`: high below low fails `assert_ohlc_relationships`.
- `bars_precision_violation.json`: a 5-decimal close fails tick precision.
- `fx_cross_breach.json`: EURGBP quoted 2% off the implied cross breaches the
  10bp tolerance; blocking mode raises, warning mode returns the evidence.
- `bars_missing_observation.json`: a dropped UK session fails coverage naming
  `UK1` and the date, while the two real closures pass.

Fail-closed publication was verified live: a correction carrying a negative
price fails the strict numeric contract, the WAP report reaches terminal
`failed`, and the published catalog keeps exactly one valid correction row.

## Schedules

| Schedule | Cron | Job |
|---|---|---|
| weekday ingestion | `30 5 * * 1-5` | bars + FX for the session |
| reference refresh | `0 3 * * 1-5` | security master, calendar, holdings |
| analytics rebuild | `45 5 * * 1-5` | normalized prices through exposure |
| weekly reconciliation | `0 6 * * 6` | full WAP pass over every asset |

Asset settings follow source behavior: tight freshness around the close,
merge strategies everywhere because vendor feeds resend and correct, strict
numeric contracts at ingest, and reconciliation tolerances surfaced as data
(`fx_cross_check.status`) rather than blocking gates.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino). CI-first: pytest needs no
containers (the replay server runs in-process on port 0), and the live path is
deterministic because the replay API serves generated bytes.

## Platform requirements and known semantics

Requires phlo-dagster runtime images built after #766 (glibc base), matching
the other examples. Point `MARKETS_API_URL` at any vendor-compatible feed to
switch from replay to live mode without touching workflow code.
