# phlo-transform

`phlo-transform` is Phlo's SQL transformation provider. It exposes two kinds of
assets to installed orchestrator adapters:

- declarations written with `phlo.transform.sql()`;
- every compiled model in a native [Phlo Transform](https://github.com/phlohouse/phlo-transform)
  workspace (`phlo.toml`).

The Rust engine remains the source of truth for compilation, planning,
execution, lineage, and tests. This package adapts its JSON reports to Phlo
capability specs; it does not reimplement SQL analysis.

## Install

```bash
pip install phlo-transform

# The engine binary, downloaded into .phlo/bin with a verified checksum
phlo transform install
```

`phlo transform install` follows the `PHLO_TRANSFORM_RELEASE_VERSION` setting
(default `0.1.0`) and resolves the portable release for Linux or macOS on
x86_64/arm64. Alternatively, install the engine from source with
`cargo install --path crates/phlo-transform-cli` and leave it on `PATH`.

## Declare transforms in Python

```python
import phlo


@phlo.transform.sql(
    table="silver.orders",
    depends_on=["bronze.orders"],
    materialized="table",
)
def orders() -> str:
    return "select * from bronze.orders"
```

`phlo.transform.sql()` registers an asset at import time. The package supplies
both plugin halves - the transformation provider and the asset provider - so
the declaration is discovered without any extra wiring.

## Use a native workspace

A workspace is a directory with `phlo.toml`:

```text
workflows/transforms/native/
  phlo.toml
  transforms/stg_orders.sql
  transforms/orders.sql
  tests/generated/...
```

```toml
[transform]
default_namespace = "analytics"
default_materialization = "table"

# Optional: keep ingestion -> transform dependency edges in the orchestrator.
[phlo_transform.sources]
"raw.orders" = "sling_orders"
```

Discovery checks `PHLO_TRANSFORM_ROOT`, then `transforms/`,
`workflows/transforms/`, and `phlo.toml` files under those trees. More than one
workspace requires an explicit root.

Each compiled model becomes one asset. The default asset key is the model's
last dotted name segment, which matches a dbt model name and keeps asset keys
stable when a project switches engines. Set
`PHLO_TRANSFORM_ASSET_KEY=logical` to keep the namespace prefix instead.

Materializing an asset runs:

```bash
phlo-transform --root <workspace> run --select <model> --json
```

The run report becomes a `MaterializeResult` (status, action, reasons, run and
plan ids, previous and desired versions, SQL hash, query id, counts, timings)
and one asset check per workspace test. The engine's `explain` report supplies
column-level lineage metadata for the asset. A failed model or test is
reported as a failure, not a silent skip. Unchanged models are reported as
skipped by the engine and succeed. Set the orchestrator tag
`phlo/full_refresh_asset=<asset key>` or pass `--force` to rebuild anyway.

In orchestrated runs the adapter comes from the resolved Phlo query engine:
the Trino endpoint and catalog come from the query-engine capability, and a
non-main runtime ref is provisioned to its own catalog through
`RefQueryCatalogManager` before the engine runs. Without orchestration the
engine decides: Trino when an endpoint is configured, otherwise DuckDB.

## Observatory candidate execution

The editor saves immutable SQL revisions and runs them on isolated candidate
branches. Compilation uses the environment's configured preview reference,
catalog, endpoint, and identity. Custom primary references are supported when
that complete binding matches the native provider configuration.

A rejected read-only compilation does not imply an uncertain write. After
correcting its prerequisites, you can confirm a new execution with a fresh
idempotency key. Once candidate provisioning or execution starts, an uncertain
outcome remains blocked, including requests with a different key.

Successful executions retain their native report. When the candidate result
hash is recorded, the authorised execution preview endpoint can retry result
readback without rerunning the model.
The authorised execution `POST .../executions/{execution_id}/reconcile`
endpoint rereads the recorded report and pinned provider snapshot. It can
resolve an uncertain operation response only when a durable native success
report, output branch hash, source revision, and provider evidence agree.
It never invokes the engine. A `running` execution, a missing report, or a
missing output hash remains unresolved. Preserve those records, branches,
and catalogs for operator investigation. Do not clear idempotency state or
assume that a timeout means no write occurred.

Execution history exposes stale evidence separately from current release
eligibility. A changed base does not hide a pinned candidate result, but a
changed candidate hash or revoked source access hides result rows.

Draft revisions and terminal executions support authorised `POST .../archive`
with `{"archived": true, "reason": "Superseded review"}`. Setting `archived` to
`false` restores visibility. History accepts `include_archived=true`.
Archiving retains all source bytes, reports, candidate bindings, and operation
fences. It does not reclaim disk space or reset the global history limits.
Archived revisions cannot compile, execute, or extend the draft until restored.

Archived revisions and terminal executions also support `POST .../reclaim` with
`confirmed: true`, a reason, and `expected_revision_digest`. This action
irreversibly deletes the local workspace copy. It retains SQL for source
handoff, per-file checksums, revision identities, reports, candidate bindings,
and operation claims. Completed reclamation frees a retained-workspace slot;
archive alone does not. History still includes the durable tombstone.

Reclamation refuses prepared, running, or unknown executions, unknown reference
metadata, and pending or unknown operation claims. The API holds the operation
claim transaction lock during local deletion. Repeating confirmed reclamation
can finish interrupted local cleanup, but never reruns a provider mutation.
Reclaimed revisions and executions cannot be restored or executed again.
No remote branches, tables, catalogs, or live data are deleted.

The API now applies configured `audit.retention_years` to supporting payloads
as well as the existing audit-preservation checks. Native workspace age uses
the later of creation and archival, since native output files can be newer
than preparation. The minimum expires at the calendar-year anniversary;
29 February expires on 1 March in a non-leap year. Unset retention adds no
age minimum. Invalid timestamps or unavailable evidence block reclamation.
This check does not expire audit records or tombstones.

`GET .../drafts/{draft_id}/{revision}/handoff` exports the saved SQL with its
project-relative path and checksum. The optional `execution_id` includes
candidate evidence and links to existing branch checks, trial merge, and
signed merge operations. These links describe POST operations, not automatic
actions. Source review and a source commit remain separate from the signed
data release. The handoff never publishes SQL or promotes candidate data.

## CLI

```bash
phlo transform status                       # active provider, workspace, counts
phlo transform check                        # compile and diagnose
phlo transform plan --select analytics.orders
phlo transform run --select analytics.orders --force
phlo transform test --select analytics.orders
phlo transform list
phlo transform explain analytics.orders
phlo transform lineage --format openlineage
phlo transform doctor
phlo transform translate --from dbt --out generated/
```

Every command forwards `--json` to the engine.

## Switching providers in place

A project can keep a dbt project and a native workspace side by side:

```bash
phlo transform use phlo-transform   # writes .phlo/transformation-provider
phlo services restart dagster       # reload the asset graph
phlo transform use dbt              # switch back
phlo transform use --unset          # follow install defaults
```

Selection order is `PHLO_TRANSFORMATION_PROVIDER` (process or project env),
then `.phlo/transformation-provider`, then install defaults. Unset means dbt
owns the graph when a dbt project exists; otherwise the native workspace does.
Only one provider contributes transform assets at a time, so asset keys cannot
collide.

## Configuration

| Variable | Purpose |
| --- | --- |
| `PHLO_TRANSFORMATION_PROVIDER` | Active provider: `dbt` or `phlo-transform`. |
| `PHLO_TRANSFORM_BIN` | Engine executable path. |
| `PHLO_TRANSFORM_ROOT` | Workspace containing `phlo.toml`. |
| `PHLO_TRANSFORM_ADAPTER` | `trino` or `duckdb`; inferred otherwise. |
| `PHLO_TRANSFORM_DUCKDB_PATH` | DuckDB database file for local runs. |
| `PHLO_TRANSFORM_TRINO_ENDPOINT` | Trino endpoint override. |
| `PHLO_TRANSFORM_TRINO_USER` / `_PASSWORD` / `_CATALOG` / `_SCHEMA` | Trino connection overrides. |
| `PHLO_TRANSFORM_JOBS` | Maximum concurrent model builds. |
| `PHLO_TRANSFORM_ASSET_KEY` | `leaf` (default) or `logical`. |
| `PHLO_TRANSFORM_RELEASE_VERSION` | Release installed by `phlo transform install`. |
| `PHLO_TRANSFORM_TIMEOUT_SECONDS` | Per-command timeout. |

Values may live in `phlo.yaml` `env:`, `.phlo/.env`, or the process
environment; earlier sources are overridden by later ones.

## Support status

This package is `outside_v1` / `development_only` in the support manifest: it is
production-shaped and tested, but not part of the v1 support contract.
