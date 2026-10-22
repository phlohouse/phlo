# Mission Control lakehouse

A synthetic CGT-04 fixture for exercising Observatory against real Iceberg tables,
Dagster runs/checks, reviewed CSV loads and native SQL candidate execution. It
reproduces the useful data grains from the Mission Control designs without
inventing run history, performance measurements or production identities.

This project resolves every Phlo distribution to the surrounding source checkout
through relative editable paths. Third-party dependencies use the example lock
file. Keep it inside `examples/lakehouses/mission-control`; copying it away from
the repository requires updating those source paths.

## Generate and verify the source files

With the repository development environment already installed:

```bash
../../../.venv/bin/python scripts/generate_fixtures.py --as-of 2026-10-22T18:00:00+00:00
../../../.venv/bin/python -m pytest tests -q
```

For this example's own environment, run `uv sync --locked --group dev`, then use
`uv run python`/`uv run pytest` instead. A fixed `--as-of` makes generated files
byte-deterministic. Omitting it anchors telemetry relative to the current clock.
Generation writes only ignored `data/` files and resets the local failure flags;
it starts no services and performs no lakehouse writes.

| Source | Generated rows | Deliberate scenario |
| --- | ---: | --- |
| Bioreactor telemetry | 2,880 | 24 runs, 504 minute groups; old source timestamps |
| LIMS samples | 96 | 37 out-of-spec results across 10 batches |
| Plate reads | 384 | 96 samples, four replicates each |
| ERP batches | 24 | Ten held, 14 released |
| Stability CSV | 1,284 | 11 columns; 1,201 UK dates, 83 ISO dates, 23 ND and 23 `<LOQ` values |

All names, laboratory-shaped readings and source events are synthetic. No
clinical, manufacturing-release or scientific conclusions follow from them.

## Create a fresh runtime

Use a separate project with fresh volumes. The 14 baseline definitions explicitly
write their project catalog's `main` branch and overwrite their own tables on a
repeat bootstrap. Do not point this example at a lakehouse containing other data.

From this directory, after reviewing the generated configuration:

```bash
uv run phlo services init --dev
uv run phlo services start --build
```

The local acceptance deployment built Phlo and its providers from source. A fresh
container deployment of this curated example has **not** been acceptance-tested;
source dependency paths may need the normal source-image build context. The
portable generator and definition tests do not prove container startup.

Configure authentication, canonical authorization and sandbox preview through the
normal project setup. This example contains no grants, tokens, passwords, private
CA, generated service state or runtime binary. Query and native compile require
an authenticated read-only preview catalog/identity and verified TLS. Native
execution additionally requires a source-built Phlo Transform engine and a
separate writable coordinator with its catalog directory available for binding
attestation. Follow the engine's source build instructions; no binary is bundled.

Open the configured Dagster UI and run `mission_control_full` once, preserving the
actual job origin, sandbox environment and `phlo/ref=main` run identity. This builds
four bronze, six silver and four gold tables in dependency order. There are eight
declared jobs; Dagster adds its automatic asset job for nine visible jobs. No
schedule is enabled. Run `audits_qc` to evaluate six real checks: the fixture is
designed for four passing checks, stale source freshness and 37/96 QC failures.

To expose genuine schema failures after the baseline exists:

```bash
uv run python scripts/set_scenario.py schema-failures
```

Run `ingest_bioreactor` and `ingest_plate_readers` explicitly in Dagster. They fail
before committing an Iceberg write. `set_scenario.py baseline` restores only the
input flags; it does not launch a recovery run or change existing snapshots.

## Exercise candidate workflows

1. In Sources, upload `data/stability_CGT-04_T6M.csv`. Review the day-first date
   choice and retain original `result` text with derived numeric/flag columns.
   Validate the whole file, prepare a new bronze destination and confirm its
   one-off candidate load. Check the actual run and preview. Expected persisted
   results are 1,284 unique rows, 23 ND, 23 `<LOQ`, 46 null numeric results and one
   normalized date (`2026-09-24`).
2. In Transform, open `review.bioreactor_runs_1min`. Save two immutable revisions,
   change DO rounding from one decimal to two, and compare the saved revision
   with source. Compile, then explicitly run the selected model as a candidate.
   Expect 504 actual minute groups from existing `do_pct` data. This does not
   implement a `do_sat_pct` schema repair or Git commit.
3. In Sources → Loads, inspect creator-private CSV receipts and failed attempts
   alongside authorized baseline ingestion-run observations. Different grains
   are not summed; absent file/package identity, bytes or timestamps stay unknown.

Candidate contracts and workflow files are authored by the current API from the
new upload/review identity. Never copy an old UUID-bound contract or candidate
catalog from another deployment. Actual candidate operations require the existing
canonical dataset/object/asset/branch permissions and project/operate scopes.
They preserve `main`, use immutable intent and exact run/ref/snapshot evidence,
and never promote or merge automatically. Do not relax policy or signature/MFA
requirements to reproduce a screenshot.

XLSX sheets, watched folders, append-to-existing loads, provider-wide load-package
history/policy/replay, native downstream/test/Git-commit workflows and publication
remain outside this example. [ACCEPTANCE.md](ACCEPTANCE.md) separates the prior
live acceptance from the portability checks performed on this curated project.
