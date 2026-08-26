# Federated domains lakehouse

Three independent domain lakehouses - sales, finance, operations - sharing one
phlo project. Each domain owns its ingestion assets, quality gates, schedules,
and a COMPLETE dbt project with a unique project name, its own profiles, and
its own selectors.

The example exists to answer one question: when several teams ship independent
dbt projects inside one workspace, what does phlo actually federate today -
and where exactly does the single-active-project runtime break? It probes that
boundary on the blessed stack and records the precise gap list in
[FEDERATION_FINDINGS.md](FEDERATION_FINDINGS.md).

## What it exercises

| Area | Coverage |
|---|---|
| Ingestion | Three `phlo.ingest.dlt` assets with deliberately different contracts: sales merges a CRM snapshot by `deal_id` (partitioned=False reference-style), finance appends day-partitioned invoice batches on `issued_on`, operations merges incident upserts by `incident_id` |
| Transforms | Three fully valid dbt projects (`sales_domain`, `finance_domain`, `operations_domain`), each with unique name, own `profiles/profiles.yml`, `selectors.yml`, sources mapped to the shared asset graph via `meta.phlo_asset_key`, and one model (`deal_pipeline`, `invoice_aging`, `incident_summary`) |
| Quality | Pandera contracts plus blocking promotion gates via `quality_checks=[...]`: deal stage vocabulary and id uniqueness, invoice amount positivity AND cross-domain known-deal attribution against the sales extract, incident severity scale and resolution consistency; labeled failure fixtures per invariant |
| Orchestration | One stopped schedule per domain plus a weekly coordinated WAP schedule spanning every asset |
| Boundary probe | `scripts/probe_federation.py` enumerates discovered projects, prints which single project would be active, and verifies FEDERATION_FINDINGS.md covers everything |

## The federation boundary (read this first)

All three dbt projects are discovered and all three parse cleanly with
dbt-trino, but the runtime activates EXACTLY ONE:

```bash
uv run python scripts/probe_federation.py
# Discovered dbt projects (3):
#   - workflows/finance/transforms/dbt    <- default ACTIVE (lexicographic first)
#   - workflows/operations/transforms/dbt <- inert
#   - workflows/sales/transforms/dbt      <- inert unless explicitly activated
```

Default activation lands on `finance_domain` purely because of alphabetical
path order - not because anyone chose it. Sales becomes end-to-end buildable
only through explicit activation, one project at a time:

```bash
DBT_PROJECT_DIR=workflows/sales/transforms/dbt uv run phlo materialize dlt_sales_deals
```

Finance's `invoice_aging` model joins the SALES domain's raw deals table.
Because dbt `ref()` cannot cross project manifests, the join is declared as a
local raw-table source whose `phlo_asset_key` points at `dlt_sales_deals`.
That hop works only as a raw-table read while finance is active; the
cross-domain join resolution remains unresolved under single-project
activation. The full verified behavior record and the required product work
(multi-manifest support, namespaced asset keys, cross-project lineage,
coordinated WAP) live in [FEDERATION_FINDINGS.md](FEDERATION_FINDINGS.md).

### Per-capability reality check

| Capability | Supported today | Notes |
|---|---|---|
| Multi-domain ingestion in one repo | Yes | Asset keys stay distinct when table names differ |
| Per-domain dbt projects as artifacts | Yes | All three parse (`dbt parse` verified); selectors resolve |
| Simultaneous multi-project activation | No | Single active manifest; N-1 projects stay inert |
| Intentional default selection | No | Default is lexicographic-first discovery, silently |
| Explicit single-project activation | Yes | `DBT_PROJECT_DIR` env var or configured `dbt_project_dir` |
| Cross-project dbt `ref()` / model lineage | No | Raw-table source declarations only; no foreign model nodes |
| Cross-domain quality gating at ingestion | Yes | Finance's blocking known-deal gate reads the sales extract |
| Cross-project lineage in the asset graph | Partial | Source-to-ingestion-asset mapping exists; model-level edges do not span manifests |
| Coordinated WAP across domains | No | Weekly job selects every asset but dbt materializations cover only the active project's models |
| Asset-name collision protection | No | Duplicate table names across domains would merge silently |
| Namespaced target schemas | No | Every profile targets `iceberg.raw`; simultaneous activation would interleave domains |

## Layout

```text
scripts/generate_fixtures.py     deterministic fixtures: CRM extract, invoices, incidents, labeled failures
scripts/probe_federation.py      multi-project discovery probe + findings verifier
workflows/sales/                 CRM snapshot ingestion, stage/uniqueness gates, daily schedule
workflows/sales/transforms/dbt/  sales_domain project: deal_pipeline model, trino profile, selectors
workflows/finance/               invoice stream ingestion (append, day-partitioned), attribution gates
workflows/finance/transforms/dbt/  finance_domain project: invoice_aging model joining sales raw deals
workflows/operations/            incident upsert ingestion, severity/resolution gates
workflows/operations/transforms/dbt/  operations_domain project: incident_summary model
workflows/schedules/             coordinated weekly WAP schedule (stopped)
tests/                           fast deterministic contract/failure/boundary tests
FEDERATION_FINDINGS.md           committed gap record: verified behavior + product work needed
```

## Run the checks

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run pytest -q
uv run ruff check .
uv run --locked ruff format --check .
uv run python scripts/probe_federation.py --check
```

Every command above is container-free and deterministic. Verify all three
manifests parse without any platform running:

```bash
for d in sales finance operations; do
  uv run dbt parse --project-dir workflows/$d/transforms/dbt \
    --profiles-dir workflows/$d/transforms/dbt/profiles
done
```

## Expected results

Fixtures describe one business month ending 2026-08-31:

- `sales/deals.csv`: 12 deals (`DL-1001`..`DL-1012`) totalling 142500.00 USD;
  stages hold won 3, lost 2, qualification 2, proposal 2, negotiation 2,
  prospecting 1.
- `finance/invoices.json`: 8 invoices (`INV-2001`..`INV-2008`) totalling
  19400.00 USD, each attributing to one of the first eight deals. Against the
  fixed aging horizon 2026-08-31 the buckets classify as paid 3, current 1,
  1-30 days 1, 31-60 days 2, 60+ days 1 - so `invoice_aging` returns five
  rows once built under finance activation.
- `operations/incidents.csv`: 10 incidents (`INC-3001`..`INC-3010`) across
  checkout/payments/inventory at sev1-sev4; 5 open, 5 resolved with durations
  15/41/67/93/119 minutes - so `incident_summary` returns ten service x
  severity rows once built under operations activation.
- Regeneration is byte-stable: rerunning the generator reproduces identical
  files (proven by test).
- Discovery finds exactly 3 dbt projects; default activation selects
  `workflows/finance/transforms/dbt`; explicit activation honors
  `DBT_PROJECT_DIR` for any single project (all pinned by tests).

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks exactly ONE
invariant, proven by tests:

- `deals_invalid_stage.csv`: stage `archived` fails the blocking
  `assert_stage_in_pipeline` gate while still passing the schema contract and
  the uniqueness gate.
- `invoices_unknown_deal.json`: invoice `INV-2999` references deal `DL-9999`,
  absent from the sales extract, and fails the blocking
  `assert_known_deals_only` attribution gate while passing schema and amount
  checks.
- `incidents_negative_duration.csv`: resolved incident `INC-3999` carries
  resolution_minutes `-5` and fails `assert_resolution_consistency` while
  passing schema and severity checks.

## Schedules

Four schedules register with Dagster, all STOPPED so an example checkout never
launches work unexpectedly:

| Schedule | Cron | Job |
|---|---|---|
| sales domain nightly | `10 2 * * *` | merge CRM snapshot, rebuild deal pipeline |
| finance domain nightly | `25 2 * * *` | append issued-day invoices, rebuild aging |
| operations domain nightly | `40 2 * * *` | merge incident state, rebuild summary |
| coordinated WAP | `0 3 * * 1` | full pass over every registered asset |

The coordinated job's selection spans all domains, but its dbt materializations
remain bounded by single-project activation - recorded as gap item "coordinated
WAP across projects" in FEDERATION_FINDINGS.md.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino). CI-first: pytest, fixture
generation, the probe, and dbt parsing need no containers. Live materialization
of the active project follows the standard path (`phlo services init/start`,
then `phlo materialize ...` with `--partition YYYY-MM-DD` for the partitioned
finance asset); non-partitioned sales and operations assets materialize without
partition keys.

## Platform requirements and known semantics

Requires the same runtime images as the other examples. DLT normalizes
ISO-8601 strings to timestamps during staging, so temporal contract fields are
typed `Series[datetime]`. Undeclared source columns are dropped with a warning;
every kept column is declared in the Pandera contracts. The finance known-deal
gate reads the sales fixture extract from disk at materialize time; point both
domains' fixture directories elsewhere to swap sources without touching
workflow code.
