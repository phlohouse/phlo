# dbt federation findings — federated-domains example

Status: boundary verified on 2026-08-24 against the pinned stack
(`phlo==0.14.0`, `phlo-dbt @ main`, `phlo-dlt`/`phlo-dagster @ 0bcb8b8ede`).
Every claim below was reproduced by `scripts/probe_federation.py` and pinned
by `tests/test_federated_domains.py`. This file is the precise gap record the
example exists to produce; it is committed and checked by tests.

## What was probed

Three structurally independent domain projects live in ONE phlo project:

| Domain | Ingestion | Own dbt project |
|---|---|---|
| sales | CRM deal snapshot, merge by `deal_id` | `workflows/sales/transforms/dbt` (`sales_domain`) |
| finance | invoice stream, append, day-partitioned | `workflows/finance/transforms/dbt` (`finance_domain`) |
| operations | incident upserts, merge by `incident_id` | `workflows/operations/transforms/dbt` (`operations_domain`) |

Each project has a unique project name, its own `profiles/profiles.yml`
(trino/iceberg/raw), its own `selectors.yml`, and one model. All three parse
as valid dbt projects (`dbt parse` shape is verified structurally in tests;
only the active project is ever compiled by the runtime).

Probe command:

```bash
uv run python scripts/probe_federation.py
```

Observed output (abridged, deterministic):

```text
Discovered dbt projects (3):
  - workflows/finance/transforms/dbt (project: finance_domain)
  - workflows/operations/transforms/dbt (project: operations_domain)
  - workflows/sales/transforms/dbt (project: sales_domain)

Default activation (no DBT_PROJECT_DIR): workflows/finance/transforms/dbt ACTIVE
Explicit activation via DBT_PROJECT_DIR (one at a time only):
  - DBT_PROJECT_DIR=workflows/sales/transforms/dbt -> ACTIVE
Verdict: single-active-project runtime; 2 of 3 discovered projects remain inert.

Note: the excerpt above is abridged for readability; run the probe script for
the byte-exact report. Discovery order is lexicographic: finance,
operations, sales.

## Verified current behavior

1. **Discovery sees everything.** `phlo_dbt.discovery.find_dbt_projects(root)`
   returns all three project directories. Multi-project discovery itself is
   not the limitation.
2. **Activation picks exactly one.** `get_dbt_project_dir()` resolves:
   (a) `DBT_PROJECT_DIR` env var if set; (b) otherwise the FIRST element of
   the discovered list; (c) otherwise the default `workflows/transforms/dbt`.
   With no env var, this example defaults to
   `workflows/finance/transforms/dbt` purely because it sorts first.
3. **There is no shallowest-path rule in discovery.**
   `find_dbt_projects` orders `rglob("dbt_project.yml")` hits lexicographically.
   The separate runtime path (`phlo_dbt.settings.DbtSettings.dbt_project_path`)
   sorts by `(len(parts), str(path))`, i.e. shallowest-then-alphabetical. The
   two implementations agree here only because all three candidates sit at
   equal depth; nested layouts (e.g. `workflows/teams/east/sales/...`) would
   make them disagree. Two tie-breaking rules for one concept is itself a
   federation hazard.
4. **The inactive projects stay valid but inert.** `finance_domain` and
   `operations_domain` manifests are never compiled, their models never get
   asset specs, and nothing materializes them. Only sales becomes end-to-end
   buildable when explicitly activated via `DBT_PROJECT_DIR` (or an equivalent
   configured `dbt_project_dir`), one project at a time.
5. **Cross-domain reads work at the raw-table layer only.** Finance's
   `invoice_aging` joins the sales raw table through a locally-declared source
   whose `meta.phlo_asset_key: dlt_sales_deals` points at the sales domain's
   ingestion asset in the shared Dagster asset graph. What does NOT exist is
   cross-manifest lineage between dbt MODELS: `ref()` cannot reach a foreign
   project's model, and activating finance means sales' `deal_pipeline` is
   absent from the definitions (and vice versa). The cross-domain join
   resolution attempt therefore remains UNRESOLVED under single-project
   activation: each half of the join compiles only when the other half of the
   lineage is not managed.

## Consequences observed

- Default checkout silently activates a domain nobody chose (finance here).
  Nothing warns that two discovered projects were skipped.
- "Coordinated WAP" (weekly `federated_domains_wap_job`) selects every
  registered ingestion asset, but dbt-backed assets only ever come from the
  single active manifest, so a coordinated publish across domains cannot be
  expressed.
- Asset keys are unprefixed table names (`dlt_sales_deals`). Two domains
  ingesting a same-named table (e.g. both defining `customers`) would collide
  silently at the Dagster asset-graph level; there is no namespace or
  registration-time collision check.
- All three profiles target `catalog=iceberg, schema=raw`. Fine while one
  project is active; simultaneous activation would interleave unrelated
  models in one schema with no per-domain namespacing.

## Product work needed for safe federation

1. **Multi-manifest support.** Build dbt asset specs from every discovered
   project (or an explicit allow-list), not from one resolved
   `dbt_project_dir`. Requires per-project target/profiles resolution and
   per-project partial-parse caches.
2. **Namespaced asset keys.** Prefix dbt-derived asset keys with the dbt
   project name (or a declared domain key), e.g.
   `sales/deal_pipeline`, and apply the same namespacing to ingestion asset
   keys or add a registration-time collision check so duplicate table names
   fail loudly instead of merging silently.
3. **Cross-project lineage.** Promote the `meta.phlo_asset_key` convention
   into first-class cross-project references: validate that the referenced
   asset key exists in the shared graph, emit explicit errors when a foreign
   key is missing, and support dbt-level `ref()` semantics across projects
   (e.g. package-style dependencies or manifest composition) so
   `invoice_aging -> deal_pipeline` lineage is real rather than a raw-table
   side effect.
4. **Coordinated WAP across projects.** A federation-aware publish job must
   orchestrate branch writes/promotions across multiple manifests in one
   dependency-correct pass, with per-domain promotion gates (today a WAP job
   can only cover the active project's models).
5. **One discovery policy.** Collapse `find_dbt_projects` (lexicographic) and
   `DbtSettings` auto-discovery (shallowest-then-alphabetical) onto a single
   ordering rule, and make default multi-project activation either explicit
   (require configuration) or loudly logged.
6. **Per-domain schema isolation guidance.** When multiple projects become
   activatable simultaneously, generated profiles need per-project schemas
   (or catalog-level namespaces) so independent domains do not share `raw`.

## Unresolved items

- Cross-domain join resolution under single-project activation: documented
  attempt (finance's local `sales_raw.sales_deals` source declaration +
  `phlo_asset_key` mapping) works only as a raw-table hop and only when
  finance is the active project; there is currently no configuration under
  which both halves compile together. Revisit after items 1 and 3 ship.
- End-to-end build evidence for finance/operations models: they remain
  valid-but-inactive artifacts; the integrator can build sales end to end via
  `DBT_PROJECT_DIR=workflows/sales/transforms/dbt`, but no container-free
  harness exists here to build all three without running the platform twice
  with different activation settings.
