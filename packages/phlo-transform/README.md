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
Executions left `running` or `unknown` do not have a supported automatic
reconciliation or retry path. Preserve their records, branches, and catalogs
for operator investigation. Do not clear idempotency state or assume that a
timeout means no write occurred.

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
