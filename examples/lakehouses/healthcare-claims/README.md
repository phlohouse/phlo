# Healthcare Claims lakehouse

A bounded-domain Phlo lakehouse for regulated claims processing: daily CSV
claim arrivals, a pipe-delimited eligibility file, and a JSON provider
directory land in Iceberg through blocking contracts, and curated utilization
and cost marts publish only privacy-safe aggregates. It exists to answer one
question: do valid claims promote end to end while invalid partitions stay
isolated with useful but non-sensitive diagnostics?

The project owns its uv environment, deterministic fixtures, workflow
configuration, and quality rules. It does not depend on another example's
runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Domains | `claims`, `eligibility`, and `providers` own ingestion and quality; `shared/contracts` holds the Pandera models and the curated-output privacy rule; one central dbt project holds the SQL |
| Ingestion | `phlo.ingest.dlt` assets: claims append per arrival day (versions accumulate), eligibility merges by period key, providers merge by id |
| Transforms | Version collapse to latest claim, procedure-code array normalization (one row per code), temporal eligibility join, provider-month utilization, monthly cost summary |
| Quality | Reconciliation rides in the blocking contract (allowed within billed, paid within allowed); domain checks cover duplicate versions, overlapping coverage periods, and uncovered service dates with masked identifiers |
| Privacy | Curated marts are aggregate-only; the privacy contract forbids member identifiers in curated columns, enforced in tests and verified against catalog columns |
| Scheduling | Daily arrival at 02:10, ordered downstream rebuild at 02:40, monthly full WAP reconciliation; all stopped by default |
| Data plane | Iceberg tables in MinIO via Nessie catalog, Trino query engine, WAP branch promotion |

## Layout

```text
scripts/generate_fixtures.py   deterministic claims, eligibility, providers, labeled failures
workflows/claims/              claim ingestion + version/reconciliation/validity checks
workflows/eligibility/         pipe-delimited eligibility ingestion + overlap checks
workflows/providers/           JSON provider directory ingestion
workflows/shared/contracts/    Pandera models and the curated privacy rule
workflows/schedules/           three stopped Dagster schedules
workflows/transforms/dbt/      central dbt models
tests/                         fast deterministic contract/failure tests
```

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run pytest -q
uv run ruff check .
```

Start the platform, then materialize in dependency order, waiting for each WAP
report in `.phlo/wap-reports/` to reach `promoted`:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor

uv run phlo materialize dlt_providers
uv run phlo materialize dlt_eligibility_periods
uv run phlo backfill dlt_claims \
  --partitions 2026-08-17,2026-08-18,2026-08-19,2026-08-20,2026-08-21 \
  --parallel 5

uv run phlo materialize stg_providers --partition 2026-08-17
uv run phlo materialize stg_eligibility_periods --partition 2026-08-17
uv run phlo materialize claims_latest --partition 2026-08-17
uv run phlo materialize claim_codes --partition 2026-08-17
uv run phlo materialize valid_claims --partition 2026-08-17
uv run phlo materialize provider_utilization_monthly --partition 2026-08-17
uv run phlo materialize claim_cost_summary --partition 2026-08-17
```

## Expected results (verified end to end)

Five arrival files hold 44 raw claim versions (40 claims; four claims re-file
a corrected version 2 with a lower billed amount), 15 coverage periods across
12 members, and 5 providers:

- `claims_latest` collapses the append-only history to exactly 40 claims.
  Re-ingesting an arrival day duplicates raw versions (append semantics) and
  the collapse still returns 40 - verified live after a replay.
- `claim_codes` normalizes the pipe-joined procedure arrays into 80 rows,
  one per claim and code.
- `valid_claims` holds all 40 claims: every service date falls inside its
  member's coverage period, and the temporal join picks the latest period.
- Reconciliation is exact: the curated mart's `total_paid` sums to the same
  7,664.40 as the valid claims themselves, across five provider-month rows of
  8 claims each.
- Curated outputs carry no member identifiers: the privacy rule passes on the
  model definitions and the catalog columns of both published tables.

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks one invariant,
proven by `tests/test_healthcare_claims.py`:

- `claims_amount_breach.csv`: paid above allowed fails the blocking
  reconciliation check inside `ClaimSchema` itself, so the ingestion run
  fails closed. Verified live: the WAP report reaches terminal `failed` and
  the published catalog keeps its prior 40 distinct claims.
- `claims_duplicate_version.csv`: the same claim version twice fails the
  version check, which also gates claims ingestion as a blocking
  `quality_checks` entry - a duplicated version can never promote.
- `claims_outside_eligibility.csv`: a 2024 service date fails temporal
  validity, with the member identifier masked to `mbr...` in the diagnostic.
- `eligibility_overlap.csv`: overlapping coverage periods fail the
  eligibility check.

## Schedules

| Schedule | Cron | Job |
|---|---|---|
| daily arrival | `10 2 * * *` | append the day's claim file |
| ordered downstream | `40 2 * * *` | eligibility, providers, then the full model chain |
| monthly reconciliation | `0 4 1 * *` | full WAP pass over every asset |

Asset settings follow regulated-source behavior: conservative retries (one
retry, 120s delay), long runtimes, claims owned by `claims-operations` with
`compliance-officer` and `actuarial` consumers, and blocking contracts so an
unreconciled batch can never reach publication.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino). CI-first: pytest needs no
containers, and the live path is deterministic because every fixture byte is
generated.

## Platform requirements and known semantics

Requires phlo-dagster runtime images built after #766 (glibc base), matching
the other examples. DLT normalizes ISO-8601 strings to timestamps during
staging, so temporal contract fields are typed natively. Raw claims are
append-only: re-ingesting an arrival file adds duplicate versions by design,
and `claims_latest` is the read-time deduplication point (the same pattern
the e-commerce example uses for Sling incrementals).
