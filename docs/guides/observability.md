# Observability with phlo-observe

How Phlo emits canonical operational telemetry, how to enable it, and how to
instrument new operations.

## Architecture

```text
Phlo process (dagster run, CLI, service)
  └─ phlo.telemetry          soft-import shim; no-ops when the SDK is absent
       └─ phlo-observe SDK   observe-core builders, ambient context, drains
            └─ HTTP drain →  phlo-observer  (ingest API → PostgreSQL → query API)
                                  └─ consumed by Observatory
```

Three layers, three responsibilities:

- **`phlo.telemetry`** (`src/phlo/telemetry.py`) is the only module Phlo code
  should import. It resolves the SDK through `importlib`, so on Python <3.12
  or without the SDK installed every helper degrades to a no-op and pipeline
  work is unaffected.
- **`phlo-observe-plugin`** (`packages/phlo-observe-plugin`) supplies the
  hook-bus translator (`ObserveHookPlugin`), the Dagster run-boundary sensors
  (`ObserveDagsterExtension`), and the `phlo-observer`/`phlo-observer-db-setup`
  service definitions. It is a preview package: install it explicitly via
  `phlo[observe]` (or `uv pip install phlo-observe-plugin` in this workspace).
- **`phlo-observer`** is the sibling-repo service that ingests canonical
  events into PostgreSQL and serves the V2 query API Observatory consumes.

### What is already instrumented

| Operation              | Event(s)                                   | Where                                        |
| ---------------------- | ------------------------------------------ | -------------------------------------------- |
| Dagster run boundary   | `pipeline.run` (terminal, per attempt)     | `ObserveDagsterExtension` run-status sensors |
| Dagster step           | `pipeline.step`                            | `phlo_dagster/adapter.py`                    |
| Asset materialization  | `asset.materialize`                        | `phlo_dagster/adapter.py`                    |
| Dagster asset checks   | `quality.check` (in-band `CheckResult` and dedicated `@asset_check`) | `phlo_dagster/adapter.py` |
| DLT pipeline run       | `dlt.pipeline.run` (terminal, `load_id`)   | `phlo_dlt/dlt_helpers.py`                    |
| dbt invocation/nodes   | `dbt.invocation`, per-model/test events    | `phlo_dbt/transformer.py`                    |
| Iceberg table writes   | `iceberg.commit` (append/merge/overwrite)  | `phlo_iceberg/tables.py`                     |
| Trino queries          | `trino.query` (sanitized hash, never SQL)  | `phlo_trino/resource.py`                     |
| WAP lifecycle          | `wap.branch.create`, `wap.validate`, `wap.promote`, `wap.reject`, `wap.cleanup` | `wap_launch.py`, hook translation |
| Ingestion/transform/quality/publish/lineage/migration hook events | canonical translations | `ObserveHookPlugin` |

The Dagster sensors run in the daemon and fire on terminal run status; the
hook plugin translates inside the worker process. Both are registered through
phlo plugin entry points, so installing the package is the only wiring.

## Local setup

```bash
# 1. Install the plugin so the observer service is discoverable
uv pip install "phlo[observe]"        # or: uv sync (dev group includes it)

# 2. Render the observability profile (adds phlo-observer + db setup)
phlo services init --profile observability     # new project
phlo services add --profile observability      # existing stack

# 3. Point producers at the observer, then re-render + start
echo 'OBSERVE_HTTP_ENDPOINT=http://localhost:10010/v1/events' >> .phlo/.env.local
phlo services add --profile observability
phlo services start
```

`OBSERVE_HTTP_ENDPOINT` is read **at compose generation time**: the dagster
and dagster-daemon definitions map it to the in-cluster address
`http://phlo-observer:8080/v1/events`. Exporting it after generation has no
effect — re-run `phlo services add` to re-render.

The SDK itself is not on PyPI yet and requires Python >=3.12. The dev
environment resolves it automatically: `uv sync` pulls `observe-core`,
`observe-query`, and `phlo-observe` from the pinned V2 commit in
`pyproject.toml`'s `[tool.uv.sources]` (marker-gated to `python_version >=
'3.12'`; on 3.11 the markers exclude them and the observability tests skip).
For generated Dagster images pass the workspace git requirements as the
`PHLO_OBSERVE_SDK` build arg (see
`packages/phlo-dagster/src/phlo_dagster/Dockerfile`); the image then also
installs `phlo-observe-plugin`. Without the SDK the shim stays inert and
pipelines run exactly as before.

### The observer image

No published `phlo-observer` image supports V2 yet — `0.1.0` and `latest`
both reject `schema_version: "2.0"` envelopes. The service definition
therefore **builds** the observer from the sibling repository at the pinned
V2 commit (`91a32fa`, the current `main`) instead of pulling an image:

```yaml
build:
  context: https://github.com/phlohouse/phlo-observe.git#91a32fa29ce19aac0e31ed3750a9090776267bd0
  dockerfile: services/phlo-observer/Dockerfile
```

`docker compose` builds remote git contexts natively, so `phlo services
start` compiles the image locally (tagged `phlo-observer:v2-91a32fa`) on the
first run — no registry access or manual checkout required. The commit SHA is
pinned, so builds are reproducible; `pull_policy: build` prevents compose
from ever pulling a stale same-named tag. When a V2 observer release is
published, replace the `build:` block with a digest-pinned `image:` and drop
`pull_policy` — the renovate rule for `ghcr.io/phlohouse/phlo-observe/**`
already anticipates that reference.

## Configuration

| Variable                    | Where        | Purpose                                             |
| --------------------------- | ------------ | --------------------------------------------------- |
| `OBSERVE_HTTP_ENDPOINT`     | producers    | Ingest URL; its presence enables emission           |
| `OBSERVE_HTTP_TOKEN`        | producers    | Bearer token; must be in `PHLO_OBSERVER_INGEST_TOKENS` |
| `OBSERVE_HTTP_API_KEY`      | producers    | API-key alternative to the bearer token             |
| `OBSERVE_DRAINS`            | producers    | Explicit drain list (`console,http,...`)            |
| `PHLO_OBSERVE_ENABLED`      | producers    | `false` disables everything; `true` forces SDK defaults |
| `PHLO_OBSERVER_PORT`        | observer     | Host port for the observer (default `10010`)        |
| `PHLO_OBSERVER_DB`          | observer     | Database on the shared Postgres (default `phlo_observer`) |
| `PHLO_OBSERVER_INGEST_TOKENS` | observer   | Comma-separated accepted ingest tokens              |
| `PHLO_OBSERVER_READ_TOKENS` / `PHLO_OBSERVER_ADMIN_TOKENS` | observer | Query/admin tokens |
| `PHLO_OBSERVER_AUTH_OPTIONAL_DEV` | observer | `false` requires all three token sets at startup (default `true`) |
| `PHLO_OBSERVER_LOG_LEVEL`   | observer     | Service log level (default `INFO`)                  |

Observer auth is **all-or-nothing**: as soon as any token set is non-empty,
every surface (ingest, read, admin) requires a matching token. Leaving all
token vars empty permits unauthenticated ingest and reads — the intended
local default. For any shared deployment set `OBSERVE_HTTP_TOKEN` and
`PHLO_OBSERVER_INGEST_TOKENS` to the same value in `.phlo/.env.local`, plus
`PHLO_OBSERVER_READ_TOKENS`/`PHLO_OBSERVER_ADMIN_TOKENS` for the query and
admin surfaces. Never set read/admin tokens alone: that would lock ingest
with no configured ingest credential. The token vars are deliberately
**not** generated secrets: each `secret: true` var gets an independent
random value, which would guarantee a credential mismatch.

## Instrumenting new operations

Route everything through `phlo.telemetry` — never import `observe_core` or
`phlo_observe` directly from Phlo code. The shim keeps every call site safe
when the SDK is absent.

```python
import phlo.telemetry as phlo_observe

# One wide event per operation, emitted on scope exit.
with phlo_observe.observe("meltano.sync", category="data") as op:
    result = run_sync()
    op.set(rows_out=result.rows)

# Or a fully explicit event (sensors, reconcilers, after-the-fact emitters):
phlo_observe.emit(
    "wap.promote",
    category="wap",
    delivery="critical",
    outcome="success",
    correlation={"run_id": run_id, "branch": branch},
    entities={"branch": phlo_observe.branch_entity_id(branch, system="nessie")},
    started_at=start,
    ended_at=end,
)
```

Checklist for a new integration point:

1. Emit **one** event per operation boundary — a run, a commit, a promote —
   never inside row loops or per-record paths.
2. Prefer the existing SDK integrations (`dagster_*`, `dlt_*`, `iceberg_*`,
   `trino_*`, `wap_*`) over hand-built events; they already stamp canonical
   entities and contracts.
3. If the work happens inside a Dagster asset, do nothing extra:
   `dagster_run_scope` already binds `run_id`/`job_id`/`partition_key`/
   `asset_key` and the hook bus already forwards the rest.
4. If the work produces a Phlo hook event (ingestion, transform, quality,
   publish, lineage, migration), `ObserveHookPlugin` translates it — do not
   emit a parallel observe event.
5. Use `delivery="critical"` only for events whose loss would corrupt the
   operational history (WAP promote/reject decisions). Normal events are
   best-effort.
6. Never block on the observer. Emission is asynchronous; failures are
   logged at debug and contained.

## What belongs in a wide event — and what does not

**Emit:** operation boundaries with durable identifiers — run start/end,
load completion, table commits, quality verdicts, promote/reject/cleanup
decisions, schema and data migrations.

**Do not emit:** per-row or per-batch data, payloads containing raw SQL or
record content, high-frequency polling ticks, or anything already carried by
a hook event. Debug and diagnostic detail stays in structured logs
(`phlo.logging`); `LogEvent`s on the hook bus are deliberately *not*
translated — logs are the high-volume diagnostic plane, observe events are
the low-volume operational history. A useful rule: if losing the event would
make a run's history incomplete in the observer, it is a wide event; if it is
only useful while debugging, it is a log.

## Correlation conventions

- `correlation.run_id` is the **physical** execution id: the Dagster run id,
  the DLT `load_id`, the dbt `invocation_id`. Each attempt owns its own
  `run://<producer>/<id>` row so the observer's monotonic run-status
  precedence cannot pin a retried run to a stale failure.
- The **logical** Phlo run id (the `phlo/run_id` tag / retry chain root) is
  preserved as `attributes.phlo_run_id` for grouping retries.
- Entities use canonical ids only: `run://dagster/<id>`, `asset://<key>`,
  `table://<name>`, `branch://<system>/<ref>`, `snapshot://<table>/<id>`,
  `model://dbt/<name>`. `<system>` is the catalog that owns the ref —
  `nessie` for branch WAP, the resolved provider (e.g. `polaris`) for
  snapshot WAP — never a parallel Phlo convention.
- Inside a `dagster_run_scope`, `bind_context` carries correlation across
  helper boundaries; the dbt run-results emission happens inside that scope,
  so `dbt.invocation`/model/test events inherit the run's job/partition/asset
  correlation while their own `invocation_id` owns the `run://dbt/<id>` row.

## Performance

`tests/observability/test_observe_dagster_e2e.py` measures whole-pipeline
overhead: the same asset materialization (ingestion hook events, a check
result, a materialization — the full observe event path) executed through
`dagster.materialize()` with the SDK enabled versus disabled.

Measured on this path: **~5.7ms added per materialization** (~14% of a
no-op asset's ~40ms of pure Dagster orchestration; against real DLT/dbt
workloads measured in seconds this is noise). The test asserts under 500ms
per materialization — a structural bound against synchronous delivery,
per-event reconfiguration, or emission inside row loops, not a
microbenchmark. When disabled, `ObserveHookPlugin._handle` returns before
any translation and the shim's scopes short-circuit, so the disabled leg
pays only a cheap flag check per call.

## Debugging without the observer

The observer is never on a pipeline's critical path. If it is down or
misconfigured:

- Events buffer in the SDK's async queue and drop after its bounds; pipelines
  continue normally and the observer shows a gap.
- `phlo_observe_configure_failed` / `phlo_observe_emit_failed` debug log
  records surface SDK-side problems in the ordinary log stream.
- To verify emission without a server, set `OBSERVE_DRAINS=console` (or
  `PHLO_OBSERVE_ENABLED=true`) in a dev shell and watch events print.
- The hook bus keeps working regardless: `FailurePolicy.LOG` contains
  translation errors, and the run-evidence store remains the source of truth
  for WAP audit state — observe events are a projection, not the record.

## Known phlo-observe gaps

Documented here rather than worked around speculatively:

- `phlo_observe.integrations.iceberg.iceberg_commit` has no branch-system
  parameter — it hardcodes `branch://nessie/<ref>`. Phlo restamps the entity
  on the scope with the resolved catalog provider (`tables.py`), but the
  `correlation.branch` value is still a bare ref name. A `system=` argument
  on the SDK helper would make this first-class.
- `phlo_observe.integrations.wap.wap_branch_create` has the same hardcoded
  `branch://nessie/<ref>` entity; `wap_launch.py` restamps it on the scope
  with the resolved catalog system, but a `system=` parameter would be
  cleaner.
- The observer's run model is per physical attempt (by design); grouping
  attempts under `phlo_run_id` for "latest attempt" views is a consumer-side
  query, not something the events can express.
- **Release gap (worked around, not resolved):** the integration emits the
  V2 (2.x) envelope and entity model, but no V2 artifacts are published —
  `phlo-observer` images `0.1.0` and `latest` are V1-only and reject
  `schema_version: "2.0"` payloads, and the `phlo-observe/v0.1.0` SDK tag
  lacks `set_entity`/`set_tag`. The deployment therefore builds the observer
  from pinned commit `91a32fa` (see "The observer image" above) and dev/test
  environments resolve the SDK from the same commit via `tool.uv.sources`.
  When a V2 release lands: pin `image:` to its digest in `service.yaml`,
  drop `build:`/`pull_policy`, and repoint the git sources at the tag.
- **OTel trace bridging:** observe events generate their own `trace_id` per
  operation scope; `HookCorrelation.trace_id` is forwarded when populated
  but nothing in Phlo stamps the active OTel trace context onto hook events
  today, so observe traces and OTel traces are parallel, not shared.

See `docs/packages/phlo-observe-plugin.md` for the service definitions and
deployment layout, and the phlo-observe repo's `docs/` for the platform spec.
