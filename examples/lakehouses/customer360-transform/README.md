# Customer 360 with two transform engines

This is the [Customer 360](../customer360/) lakehouse converted to a native
[Phlo Transform](../../../packages/phlo-transform/README.md) workspace, with
the original dbt project kept in place. Ingestion, quality contracts,
schedules, and asset keys are unchanged. Only the engine that owns the SQL
graph can change, in situ.

It exists to answer two questions with runnable evidence:

1. Does translating a real, multi-domain dbt project to Phlo Transform
   preserve its data? All ten models and fifteen tests run against the same
   raw rows and produce identical tables.
2. Can a project switch between dbt and Phlo Transform without rewriting
   ingestion, schedules, or downstream asset references? Yes - both providers
   expose the same asset keys (`stg_commerce_customers`,
   `customer_dimension`, ...), and one command flips ownership.

## Layout

```text
workflows/commerce/models/        dbt staging, identity resolution, dimension
workflows/support/models/         dbt ticket staging and engagement
workflows/marketing/models/       dbt consent staging and gated publication
workflows/transforms/dbt/         dbt project (source of truth, model-paths
                                  point at the three domain folders)
workflows/transforms/native/      generated Phlo Transform workspace
  phlo.toml                       namespace, materialisation
  transforms/*.sql                one native model per dbt model
  tests/generated/*.sql           generated singular tests
scripts/generate_native_workspace.py  re-translate dbt -> native, atomically
scripts/dbt_sources.py                carry dbt source->asset bindings across
scripts/verify_parity.py              the executable dbt/native parity proof
tests/test_customer360.py            deterministic domain contract tests
tests/test_transform_switch.py       provider selection and asset-key contract
tests/test_transform_parity.py       graph/translation integration checks
```

The dbt project compiles three model roots into one manifest:

```yaml
model-paths:
  - "../../commerce/models"
  - "../../support/models"
  - "../../marketing/models"
```

The native workspace is generated from that project with the engine's one-way
translator. It is checked in so the example runs without a translation step,
and `scripts/generate_native_workspace.py` regenerates it atomically.

## Conversion

```bash
# Analyse only: every resource is classified CLEAN / REVIEW / UNSUPPORTED.
uv run --project ../.. python scripts/generate_native_workspace.py --check

# Re-translate, verify the result compiles, and replace the native workspace.
uv run --project ../.. python scripts/generate_native_workspace.py
```

The current project translates **10 models / 5 sources / 15 tests, 100% CLEAN**.
The translator is not a dbt runtime: `ref()` becomes the model's logical
relation, `source()` becomes the physical `raw.*` relation, and dbt tests
become native assertions and generated singular tests.

The generator also carries the dbt source metadata across. Each
`meta.phlo_asset_key` in the dbt schema files becomes a
`[phlo_transform.sources]` entry in the native `phlo.toml`:

```toml
[phlo_transform.sources]
"raw.c360_customers" = "sling_c360_customers"
"raw.consent_events" = "dlt_consent_events"
```

That keeps the ingestion -> staging edges in the asset graph, so switching
engines does not change what has to materialise first.

## Prove parity

The parity script needs the engine binary and `dbt-duckdb`. Until the pinned
provider releases are on the package index, run the example against the
repository environment:

```bash
# From examples/lakehouses/customer360-transform
uv sync --locked --project ../..                # includes dbt-duckdb
uv run --project ../.. phlo transform install   # pinned portable release -> .phlo/bin
uv run --project ../.. python scripts/verify_parity.py
```

`--project ../..` keeps the current directory (and its workspace files) while
using the repository's locked environment, which includes `dbt-duckdb`.

It generates the fixtures, loads identical raw rows into two DuckDB databases,
runs `phlo-transform run` on the native workspace and `dbt run` + `dbt test`
on the untouched dbt project, then compares every table row-for-row and
column-type-for-column-type.

Verified output (DuckDB, base fixture state):

```text
table                              rows  only native  only dbt  types
----------------------------------------------------------------------
consent_current                       8            0         0   same  OK
consent_safe_product                  9            0         0   same  OK
customer_dimension                   10            0         0   same  OK
identity_resolution                  20            0         0   same  OK
stg_commerce_customers               10            0         0   same  OK
stg_commerce_orders                  30            0         0   same  OK
stg_consent_events                   13            0         0   same  OK
stg_marketing_contacts                7            0         0   same  OK
stg_support_tickets                  14            0         0   same  OK
support_engagement                    9            0         0   same  OK

PARITY PASS: 10/10 models identical row-for-row; 15/15 engine tests passed.
```

The same run against dbt-duckdb completes 10/10 models and 15/15 tests.

## Switch engines in situ

Both providers are installed. The checked-in default is dbt, so existing
projects keep the graph they already had. Switch with the project marker:

```bash
uv run --project ../.. phlo transform use phlo-transform
uv run --project ../.. phlo services restart dagster        # reload the orchestrated graph

uv run --project ../.. phlo transform status                # active provider, workspace, counts
uv run --project ../.. phlo transform use dbt               # switch back
uv run --project ../.. phlo transform use --unset           # follow install defaults again
```

`--project ../..` targets the repository environment from this directory; drop
it once the pinned provider releases are published.

`phlo transform use` writes `.phlo/transformation-provider` (local, gitignored)
and validates that the provider is installed. `PHLO_TRANSFORMATION_PROVIDER`
overrides the marker for a single process, which is useful in CI:

```bash
PHLO_TRANSFORMATION_PROVIDER=phlo-transform uv run --project ../.. python -m pytest -q
```

What changes: the ten SQL asset definitions and their execution path.
What does not: ingestion assets, asset keys, dependency edges, schedules,
quality contracts, and WAP policy.

The schedules in `workflows/schedules/customer360.py` select asset keys such as
`stg_commerce_customers` and `customer_dimension`; both providers emit those
exact keys.

When the Observatory is running, the **Transform** page shows the active
workspace, all ten models, their content-hash versions, blocking tests, and the
last run for each asset. The same inventory is available from
`GET /api/v1/transform?env=<env>`.

## Run the lakehouse

Use the repository environment while the pinned provider releases are
unpublished. The `--project ../..` flag targets it; drop the flag once the
pinned releases are on the package index.

```bash
uv sync --locked --project ../..
uv run --project ../.. python scripts/generate_fixtures.py
uv run --project ../.. python -m pytest -q
uv run --project ../.. ruff check .
```

Start the platform, seed the commerce source, and start the support replay API
in another shell:

```bash
uv run --project ../.. phlo services init --force --no-dev --profile observability
uv run --project ../.. phlo services start --build
uv run --project ../.. phlo doctor

uv run --project ../.. python scripts/seed_postgres.py
uv run --project ../.. python scripts/support_api.py            # port 8093
```

With dbt active, materialize the models with `phlo materialize <key>` as in
the Customer 360 example. With Phlo Transform active, the same command
materializes the native models: the provider shells out to
`phlo-transform run --select <model>` and maps its run report and tests into
a Dagster materialization and asset checks.

Local engine commands, without the platform:

```bash
uv run --project ../.. phlo transform check
uv run --project ../.. phlo transform plan --adapter duckdb
uv run --project ../.. phlo transform explain customer360.customer_dimension
uv run --project ../.. phlo transform lineage --format openlineage
uv run --project ../.. phlo transform doctor --adapter duckdb
```

DuckDB runs also need the raw relations. Run
`scripts/verify_parity.py --work-dir <dir>` to seed a database and report its
path, or point `PHLO_TRANSFORM_DUCKDB_PATH` at a DuckDB file that already
holds the `raw` schema.

`PHLO_TRANSFORM_ROOT` is set in `phlo.yaml`, so the workspace is found from
the host CLI and from the orchestrator container.

## Expected results

The fixture universe is nine seeded people seen under twenty distinct
addresses, plus one late signup:

- Commerce base state holds 10 customers and 30 orders ($3,595.00 total
  revenue); the update set adds 3 customer rows and 4 orders.
- Support replays 14 tickets (11 resolved, 3 open); marketing captures 7
  contacts and 13 consent events.
- `identity_resolution` collapses 20 observed addresses onto 9 canonical
  emails. Alice appears as three spellings.
- `customer_dimension` holds 10 versions and 9 current rows. After the update
  set: 13 versions and 10 current rows, with Alice's current row flipping from
  `C0002` to `C0001` and segment `business`.
- `consent_current` resolves 8 identities: Dana granted, Bob granted after
  revocation, Alice revoked, Hana never granted, Zoe absent.
- `consent_safe_product` evaluates 9 current identities: 5 exposed, 4
  suppressed with explicit reasons. After the update set: 5 exposed / 5
  suppressed out of 10.

Both engines produce exactly these numbers; the parity script asserts the
tables, and `tests/test_customer360.py` asserts the business invariants in
plain Python.

## Expected failures

The labeled fixtures under `generated-data/failures/` break one invariant
each, proven by `tests/test_customer360.py`:

- `consent_tied_timestamps.json`: latest-wins consent precedence is
  undecidable; the blocking ingestion check fails closed.
- `orders_unknown_email.json`: an order references an email no domain
  captured.
- `ticket_backdated_resolution.json`: resolution precedes creation.

## Known differences

The engines share the asset contract, not every internal concept:

- dbt assets are daily partitioned; Phlo Transform assets are not. Phlo
  Transform is state-addressed and rebuilds on content, configuration, source,
  or target change, so a daily partition key is not part of its model.
- Asset groups differ (`silver`/`gold` from dbt's naming convention versus
  the `customer360` namespace).
- Phlo Transform's `run` is state-aware: an unchanged model is reported as
  skipped and the asset succeeds, unless `phlo/full_refresh_asset` is set for
  that key or `--force` is passed.

## Platform requirements

Requires phlo-dagster runtime images built after #766 (glibc base), matching
the other examples. The commerce source runs on localhost port 10432 via
`docker-compose.yml`; override `COMMERCE_SOURCE_URL` to relocate it. The
support replay server binds `127.0.0.1:8093`; inside the platform the Dagster
services reach it through `host.docker.internal`.

The example pins the provider releases; while any of them are unpublished,
use the repository environment (where `phlo-transform` is installed from
`packages/phlo-transform`) to run the parity proof. The example ships without
a `uv.lock` for the same reason: resolve it once the pinned packages are
available.
