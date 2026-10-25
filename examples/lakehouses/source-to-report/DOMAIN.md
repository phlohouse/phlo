# Adapting the example to another domain

This example ships two packs over one spine: air quality is the reference,
and the YouTube channel is the same pipeline with a different input. Read the
YouTube pack beside the steps below to see the pattern twice; the steps
replace whichever pack you are adapting.

The example has two halves.

**The spine** is generic. It knows how to run ingestion assets, call dbt,
query the warehouse, hash inputs, look numbers up strictly, draw charts and
render a self-contained report. It contains no domain words.

**The domain pack** is the air-quality worked example. It knows what a
station, a pollutant, a monitoring note and an exceedance are. This is the
part you replace.

```diagram
┌──────────────────────────── domain pack ─────────────────────────────┐
│ vocabulary   sources      extraction   contracts   dbt     analysis  │
│ domain/__init__  domain/sources.py  domain/extraction.py            │
│ workflows/schemas/*          workflows/transforms/dbt/*             │
│ domain/analysis.py           domain/report.html.j2                  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ rows and tables
┌───────────────────────────────▼──────────────────────────────────────┐
│ spine: runner · ingest · dbt · warehouse · provenance · facts ·      │
│        charts · report                                               │
└──────────────────────────────────────────────────────────────────────┘
```

The seam is deliberately narrow: the pack produces **rows** and **tables**,
and the spine turns them into evidence, a run record and a page. Nothing in
the spine imports the pack except the runner and the domain analysis module.

## What the spine guarantees

| Guarantee | Where | What it means for a new domain |
| --- | --- | --- |
| Contracts are enforced before data lands | `ingest.py` | Your Pandera models decide what is valid. |
| dbt builds the marts | `dbt.py` | Your SQL owns the modelling; the runner only invokes it. |
| The report cannot invent a number | `facts.py` | Prose calls `facts.value(...)`, `facts.row(...)`, `facts.extreme(...)`. A missing or ambiguous figure fails the build. |
| Every input is pinned | `provenance.py` | Your source files are hashed and listed in `provenance.json`. |
| Charts are deterministic and offline | `charts.py` | Pass labels and values; no fonts or JavaScript are fetched. |
| The page is self-contained | `report.py` | External scripts, styles and links are rejected. |

## Adapt it in eight steps

Work in this order. Each step has a test that will tell you when it is wrong.

### 1. Vocabulary — `src/source_to_report/domain/__init__.py`

Replace `Station` and `Pollutant` with your entities, and the constants the
pack shares: period, title, description, thresholds, tolerance. Keep it small;
this module should read like a glossary, not a schema.

### 2. Sources — `src/source_to_report/domain/sources.py`

Write readers that return plain dictionaries. They may read a generated pull,
a local file or an API. Raise a clear error when the input is missing. Do not
validate here; the contract does that.

### 3. Contracts — `workflows/schemas/monitoring.py`

Write one Pandera model per raw table. Encode what must be true: unique ids,
allowed values, ranges, a unit that matches the measure. These models are used
by both the local runner and the orchestrator's validation check.

### 4. Ingestion assets — `workflows/ingestion/monitoring.py`

Wrap each reader in `@phlo.ingest.dlt` with a table name, unique key, owner,
consumers and its contract. Keep the function a thin adapter: read, return a
`dlt.resource`, done. If a source is disabled, say why in the module rather
than failing at runtime.

### 5. Evidence and marts — `workflows/transforms/dbt/`

Point `models/sources.yml` at the raw tables. Build the smallest useful
chain: staging (typed and limited), daily/monthly aggregates, and the mart
that joins published claims to measurements. Add `not_null`, `unique`,
`relationships` and singular tests for the invariants your domain relies on.

### 6. Insight extraction — `src/source_to_report/domain/extraction.py`

If your domain has published text, extract its figures here and keep the
locator: source path, line or page, and the sentence. Be strict. A line that
looks like a figure but cannot be parsed should stop the build; the final
count should be a known multiple of sources, entities and measures.

If your domain has no published text, delete this stage and the claims models.
The rest still works.

### 7. Analysis — `src/source_to_report/domain/analysis.py`

Read only the marts. Write one CSV per table the report needs, a
`summary.json` (counts, headlines, checks) and the charts. Use
`warehouse.relation("table")`, never a hard-coded catalog name. Raise when a
station, month or group is missing: a partial brief is worse than no brief.

### 8. Report — `src/source_to_report/domain/report.html.j2`

Keep the layout and the guarantees, change the words and the sections. Use
`facts.*` for every number and `domain.*` for thresholds. If you add a table,
add its CSV in step 7. If you add a chart, add it to the `charts` dict.

Then update `scripts/generate_fixtures.py` so the pull is deterministic, and
`scripts/verify.py` so its checks describe your ground truth. The fixture
manifest is the independent expectation: compute it in Python, not in SQL.

## Invariants worth keeping

1. **Deterministic.** A fixed seed, fixed period, no wall clock in the report.
   Two builds from the same pull must produce identical bytes.
2. **Key-free by default.** Fixtures and rules first; a live API or a model is
   an opt-in that announces itself in provenance.
3. **No silent gaps.** A missing month, a failed parse, an unmatched claim is
   an error, not a zero.
4. **Numbers come from tables.** The template may format a value; it may not
   compute one.
5. **One page.** If the report needs three services to read, it is not a
   report you can hand to someone.

## Checklist

```bash
uv run python scripts/generate_fixtures.py   # your pull is deterministic
uv run pytest                                # contracts, extraction, facts, pipeline
uv run source-to-report                      # the page builds and reads correctly
uv run python scripts/verify.py              # two builds are identical
```

If `verify.py` still describes air quality, it will pass on the old domain and
tell you nothing about the new one. Change it in the same commit as the pack.
