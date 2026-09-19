# phlo-observe-plugin

`phlo-observe-plugin` integrates Phlo with the
[phlo-observe](https://github.com/phlohouse/phlo-observe) V2 observability
platform: it translates Phlo hook-bus events into canonical `phlo-observe`
wide events, emits terminal `pipeline.run` events from Dagster run-status
sensors, and ships the `phlo-observer` ingestion/query service definition for
generated deployments.

## What It Does

- Translates ingestion, transform, quality, publish, lineage, service
  lifecycle, and migration hook events into canonical `phlo-observe` events
- Maps the WAP audit verdict (`quality.result` with `check_name="wap.aggregate"`)
  to `wap.validate`, and WAP evidence observations to `wap.promote`,
  `wap.reject`, and `wap.cleanup` with critical delivery
- Emits terminal `pipeline.run` events for Dagster run success, failure, and
  cancellation so each physical attempt owns its run row
- Registers the `phlo-observer` service (HTTP ingestion + query API on the
  `observability` profile) and the `phlo-observer-db-setup` companion that
  provisions its PostgreSQL database

## Correlation Model

`correlation.run_id` is the *physical* execution identity — the Dagster run
id, DLT load id, or dbt invocation id. A hook event's own `run_id` (the Phlo
logical run id from the `phlo/run_id` tag or root retry chain) is preserved
as `attributes.phlo_run_id`. Retries therefore get separate observer run rows
while remaining grouped by the logical id, and the observer's monotonic
run-status precedence can never pin a retried run to a stale failure.

WAP lifecycle events attach explicit `branch://<system>/<ref>` and
`run://dagster/<id>` entities, where `<system>` is the catalog that owns the
staging ref — `nessie` for the branch strategy, the resolved snapshot catalog
(for example `polaris`) for the snapshot strategy. `catalog_ref` records the
ref an operation mutated (for example `main` during promotion) while
`wap_branch` identifies the audit ref.

## Installation

`phlo-observe-plugin` is a preview package that stays out of `phlo[defaults]`:
install the opt-in extra so the hook translator, sensors, and service
definitions are discoverable wherever the `phlo` CLI runs:

```bash
uv pip install "phlo[observe]"
```

Generated Dagster images install it automatically when `PHLO_OBSERVE_SDK` is
set (see below); without the SDK the plugin is inert anyway, so the build arg
gates both.

The `phlo-observe` SDK it drives requires Python >=3.12 and is not yet
published to PyPI, so it is intentionally not a declared runtime dependency.
Development environments get it automatically: the `dev` dependency group
declares all three packages marker-gated to `python_version >= '3.12'` and
`[tool.uv.sources]` resolves them from the pinned V2 commit — `uv sync` on
3.12 installs them (and the observability e2e tests run), while 3.11 skips
both. Outside the dev environment install each unpublished workspace package
from the source repository — a single subdirectory install cannot resolve
the repo's `observe-core`/`observe-query` workspace sources:

```bash
uv pip install \
  "observe-core @ git+https://github.com/phlohouse/phlo-observe.git@91a32fa29ce19aac0e31ed3750a9090776267bd0#subdirectory=packages/observe-core" \
  "observe-query @ git+https://github.com/phlohouse/phlo-observe.git@91a32fa29ce19aac0e31ed3750a9090776267bd0#subdirectory=packages/observe-query" \
  "phlo-observe @ git+https://github.com/phlohouse/phlo-observe.git@91a32fa29ce19aac0e31ed3750a9090776267bd0#subdirectory=packages/phlo-observe-sdk"
```

The integration needs the V2 SDK (canonical `entities`/`set_entity` and the
2.x envelope). The published `phlo-observe/v0.1.0` tag predates V2, so the
example pins a V2-capable commit — replace it with a release tag once a V2
release exists. A pre-V2 SDK degrades rather than breaks: `phlo.telemetry`
logs one warning and emits events without entity links (a V1 observer
accepts them, but the entity graph is lost).

For generated Dagster images pass the same space-separated requirements as
the `PHLO_OBSERVE_SDK` build argument; the Dockerfile installs them into the
runtime (or the locked project venv for uv-managed builds).

Without the SDK every integration point degrades to a no-op via
`phlo.telemetry`; a plain install is never broken by its absence.

## Enabling Emission

Emission is environment-driven through `OBSERVE_HTTP_ENDPOINT`. The
`phlo-observer`, `phlo-dagster`, and `dagster-daemon` service definitions map
a non-empty endpoint to the in-cluster observer address — **at compose
generation time**. Set it in `.phlo/.env.local` (or the process environment)
before generating or regenerating the stack:

```bash
# New project — render the observability profile up front
phlo services init --profile observability

# Existing stack — add the profile; compose is re-rendered
phlo services add --profile observability
```

To turn emission on later, set the endpoint and re-render:

```bash
echo 'OBSERVE_HTTP_ENDPOINT=http://localhost:10010/v1/events' >> .phlo/.env.local
phlo services add --profile observability
phlo services start
```

Exporting `OBSERVE_HTTP_ENDPOINT` after the stack was generated without it
has no effect: `phlo services start` does not regenerate compose files, so
the Dagster and API containers keep running without the endpoint wiring.

## Ingest Authentication

The observer accepts ingest requests authenticated by `OBSERVE_HTTP_TOKEN`
(bearer) or `OBSERVE_HTTP_API_KEY`, matched against the observer's
comma-separated `PHLO_OBSERVER_INGEST_TOKENS`. `PHLO_OBSERVER_READ_TOKENS`
and `PHLO_OBSERVER_ADMIN_TOKENS` gate the query and admin surfaces.

Auth is **all-or-nothing**: as soon as any token set is non-empty, every
surface requires a matching token. Setting only read/admin tokens would
lock ingest with no configured ingest credential, so none of the token
variables are auto-generated secrets — each `secret: true` variable
receives an independent random value, which would leave producers and the
observer holding different credentials. Instead:

- **Local development (default):** all token vars are empty — ingest and
  reads are unauthenticated and everything works out of the box.
- **Shared/hardened deployment:** set `OBSERVE_HTTP_TOKEN` and
  `PHLO_OBSERVER_INGEST_TOKENS` to the same value in `.phlo/.env.local`,
  plus `PHLO_OBSERVER_READ_TOKENS`/`PHLO_OBSERVER_ADMIN_TOKENS` for the
  surfaces you expose, then regenerate with
  `phlo services add --profile observability`. Set
  `PHLO_OBSERVER_AUTH_OPTIONAL_DEV=false` to make the observer refuse
  startup unless all three token sets are configured.

## Runtime Layout

- `phlo_observe_plugin.hooks_plugin.ObserveHookPlugin` — hook-bus translator
- `phlo_observe_plugin.dagster_ext.ObserveDagsterExtension` — Dagster
  run-status sensors (`observe_run_success`, `observe_run_failure`,
  `observe_run_canceled`), all defaulting to RUNNING
- `phlo_observe_plugin.service.yaml` — `phlo-observer` service definition.
  No published image speaks the V2 envelope (`0.1.0`/`latest` accept only
  `schema_version` 1.x), so the service builds from the phlo-observe monorepo
  at the pinned V2 commit `91a32fa` via a remote git `build.context`,
  `services/phlo-observer/Dockerfile`, and `pull_policy: build`. When a V2
  image ships, pin `image:` to its digest and drop the build block.
- `phlo_observe_plugin.db-setup.yaml` — one-shot `phlo_observer` database
  provisioning on the shared Postgres service
