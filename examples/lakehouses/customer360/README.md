# Customer 360 lakehouse

A domain-colocated Phlo lakehouse where commerce, support, and marketing each
own their ingestion, quality checks, and SQL models - and one central dbt
project compiles all three model roots into a single lineage graph. It exists
to answer one question: when three teams capture the same people under
different email spellings and one of them owns consent, can the lakehouse
resolve one identity per person and refuse to publish anyone whose consent is
not granted?

The project owns its uv environment, deterministic fixtures, workflow
configuration, and a local commerce source database. It does not depend on
another example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Ingestion | Sling incremental replication from local PostgreSQL (`c360_customers` keyed by `email`, `c360_orders` keyed by `order_id`); DLT merges for replayed support tickets (by `ticket_id`), marketing contacts (by `email`), and consent events (by composite `event_key`) |
| Transforms | Domain-colocated dbt models compiled by ONE project with three `model-paths`: staging plus identity resolution and the type-2 dimension (`commerce/models`), ticket staging and engagement (`support/models`), consent staging and consent-gated publication (`marketing/models`) |
| Quality | Blocking Pandera contracts at ingest; domain validators over plain DataFrames including the blocking consent-precedence check on the marketing ingestion asset; labeled failure fixtures per invariant |
| Orchestration | Five Dagster schedules spanning all domains, every one registered STOPPED |
| Identity | Case-insensitive, plus-suffix-stripping canonicalization so one person seen as three address variants converges to one canonical email |
| Data plane | Iceberg tables in MinIO via Nessie catalog, Trino query engine, WAP branch promotion |

## Layout

```text
docker-compose.yml               commerce PostgreSQL source (port 10432)
scripts/generate_fixtures.py     deterministic fixtures + labeled failures
scripts/seed_postgres.py         load base/update state into the source DB
scripts/support_api.py           replay HTTP server serving ticket payloads
workflows/commerce/              sling ingestion.py, quality.py, models/
workflows/support/               DLT ingestion.py, quality.py, models/
workflows/marketing/             DLT ingestion.py, quality.py, models/
workflows/schemas/               Pandera contracts shared by the domains
workflows/schedules/             five stopped schedules across all domains
workflows/transforms/dbt/        ONE central project; models live in the domains
tests/                           fast deterministic contract/failure tests
```

Each domain owns its models next to its ingestion code:

```text
workflows/commerce/models    stg_commerce_customers, stg_commerce_orders,
                             identity_resolution, customer_dimension
workflows/support/models     stg_support_tickets, support_engagement
workflows/marketing/models   stg_marketing_contacts, stg_consent_events,
                             consent_current, consent_safe_product
```

The central project compiles them without moving any file:

```yaml
model-paths:
  - "../../commerce/models"
  - "../../support/models"
  - "../../marketing/models"
```

`dbt parse` resolves cross-root refs (`identity_resolution` unions identities
from all four staging models; `support_engagement` and `consent_safe_product`
join onto the dimension) into one manifest covering all ten models.

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run pytest -q
uv run ruff check .
```

Start the platform:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
```

Seed the commerce source, then start the support replay API in another shell:

```bash
uv run python scripts/seed_postgres.py
uv run python scripts/support_api.py            # port 8093
uv run python scripts/seed_postgres.py --update # later: apply the delta set
```

Materialize every asset in dependency order, waiting for each WAP report in
`.phlo/wap-reports/` to reach `promoted` before launching dependents:

```bash
uv run phlo materialize sling_c360_customers
uv run phlo materialize sling_c360_orders
uv run phlo materialize dlt_support_tickets
uv run phlo materialize dlt_marketing_contacts
uv run phlo materialize dlt_consent_events
uv run phlo materialize stg_commerce_customers
uv run phlo materialize stg_commerce_orders
uv run phlo materialize stg_support_tickets
uv run phlo materialize stg_marketing_contacts
uv run phlo materialize stg_consent_events
uv run phlo materialize identity_resolution
uv run phlo materialize customer_dimension
uv run phlo materialize consent_current
uv run phlo materialize consent_safe_product
uv run phlo materialize support_engagement
```

The three DLT assets and both reference-scale merges opt out of partitioning
(`partitioned=False`): contacts are small, tickets merge by id, and consent
precedence needs the complete event history per email to decide latest-wins.

Inspect results:

```bash
uv run phlo trino --execute 'SELECT count(*) FROM iceberg.raw.customer_dimension WHERE current_flag'
uv run phlo trino --execute "SELECT count(*) FROM iceberg.raw.consent_safe_product WHERE is_exposed"
uv run phlo catalog tables
```

Applying the update set and re-running the commerce replications demonstrates
incremental behavior: only watermark-newer rows move, and the dimension opens
new versions instead of overwriting history.

## Expected results (verified end to end)

The fixture universe is nine seeded people seen under twenty distinct
addresses, plus one late signup:

- Commerce base state holds 10 customers and 30 orders ($3,595.00 total
  revenue); the update set adds 3 customer rows (2 segment changes + 1 new
  signup) and 4 orders, all stamped after the base watermark.
- Support replays 14 tickets, 11 resolved and 3 open.
- Marketing captures 7 contacts and 13 consent events.
- `identity_resolution` collapses 20 distinct observed addresses onto 9
  canonical emails. Alice alone appears as `alice.anderson@example.com`,
  `Alice.Anderson+legacy@example.com`, and `ALICE.ANDERSON+orders@example.com`.
- `customer_dimension` (base) holds 10 versions - Alice legitimately has two
  because two customer records map to her - with exactly 9 current rows, one
  per identity. After the update set: 13 versions, 10 current rows, and
  Alice's current version flips from the legacy account `C0002` to the
  updated `C0001` (segment promoted to business). Validity windows are
  adjacent half-open intervals, proven nonoverlapping by pure-python
  replication in the tests.
- `consent_current` resolves 8 identities. Latest event wins: Dana flipped
  twice and ends granted, Bob recovered after revocation and ends granted,
  Alice ends revoked, Hana was never granted, Zoe has no record.
- `consent_safe_product` evaluates all 9 current identities against consent:
  5 exposed (granted), 4 suppressed with explicit reasons (revoked or no
  consent record). With Ivy's post-update signup included it is 5 exposed /
  5 suppressed out of 10.
- `support_engagement` aggregates tickets per current identity through
  canonical joins; variant-addressed tickets land on the same identity row.

Re-running `dlt_consent_events` or `dlt_support_tickets` changes nothing:
merges are keyed (`event_key`, `ticket_id`), so replays are idempotent.

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks exactly one
invariant, proven by `tests/test_customer360.py`:

- `consent_tied_timestamps.json`: two events for one email share an exact
  `occurred_at`, so latest-wins precedence is undecidable. Fails the blocking
  `assert_consent_precedence_resolvable` check on the consent ingestion asset;
  the WAP report terminates failed and nothing downstream promotes.
- `orders_unknown_email.json`: one order references an email no domain ever
  captured. Fails `assert_orders_reference_known_customers`.
- `ticket_backdated_resolution.json`: `resolved_at` precedes `created_at`.
  Fails `assert_resolved_after_created`.

To reproduce the fail-closed path live, copy the tied-timestamp events into
`generated-data/marketing/consent_events.json`, re-materialize
`dlt_consent_events`, and observe the blocked run.

## Schedules

Five schedules register with Dagster, all STOPPED so an example checkout never
launches work unexpectedly:

| Schedule | Cron | Job |
|---|---|---|
| commerce incremental | `*/20 * * * *` | replicate customers + orders |
| support/marketing ingestion | `15 * * * *` | merge tickets, contacts, consent |
| identity rebuild | `30 2 * * *` | rebuild staging, resolution, dimension |
| consent-safe publication | `45 2 * * *` | rebuild consent_current + safe product |
| weekly reconciliation | `0 4 * * 6` | full WAP pass over every asset |

Asset settings follow source behavior: commerce orders get the shortest
freshness window and deepest retry budget (frequent writes); contacts get a
weekly window (static reference file); the consent asset notifies the privacy
office on SLA breach because its freshness is a compliance property.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino) with optional Sling. The
example is CI-first: pytest needs no containers, the replay API needs no
network, and every documented live path is deterministic because inputs are
generated, not recorded.

## Platform requirements and known semantics

Requires phlo-dagster runtime images built after #766 (glibc base), matching
the other examples. The commerce source runs on localhost port 10432 via
`docker-compose.yml`; override `COMMERCE_SOURCE_URL` to relocate it. The
support replay server binds `127.0.0.1:8093`; inside the platform the Dagster
services reach it through `host.docker.internal` (configured in `phlo.yaml`).
Temporal contract fields are typed natively because DLT normalizes ISO-8601
strings during staging. Multi-root `model-paths` require dbt-core >= 1.6;
verified against dbt-trino 1.11 that one manifest spans all three roots.
