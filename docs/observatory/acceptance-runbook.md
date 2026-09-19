# Observatory acceptance runbook — disposable live fixture

MC-00 deliverable under `docs/roadmaps/observatory-real-lakehouse.md`. This is
the disposable real-stack fixture every MC-01+ acceptance claim runs against.
It replaces ad-hoc sessions against `materialize-check/lakehouse`, which is
read-only reference and must never be mutated, reset, or committed into.

## Fixture selection

**Project content:** `examples/lakehouses/wap-failure-lab` — scripted WAP
scenarios with deterministic byte-stable fixtures, owned uv environment,
container-free unit coverage, and declared `owner`/`sla`/`consumers` metadata.

**Isolation mechanism:** `phlo_testing.profile_harness.BundledStackHarness` —
allocates unique host ports per boot, creates a temporary project, runs
`phlo services init`/`start`, waits for readiness, and cleans up containers and
the project directory. Opt-in via `PHLO_RUN_BUNDLED_STACK_CONTRACT=1`;
preserved on failure via `PHLO_KEEP_BUNDLED_STACK=1`.

Coverage against the MC-00 fixture requirements:

| Requirement | Provided by | Status |
|---|---|---|
| ≥ 2 datasets | `dlt_sensor_batches` (strict), `dlt_sensor_batches_relaxed` (warn-only), `batch_summary` (dbt) | covered |
| Healthy run | `run_scenario.py valid_publish` — promoted, +12 rows, branch removed | covered |
| Quality failure | `run_scenario.py quality_failure` — blocking contract + `assert_batch_ids_unique` failures, main unchanged, branch retained | covered |
| Execution failure | `run_scenario.py retry_recovery` — injected attempt-1 fault, `max_retries=3`, counter file = 2 | covered |
| Multi-segment asset key | `phlo_asset_key` meta exists (`dlt_sensor_batches` source mapping) but no emitted asset carries a `schema.model`-style key | **delta required** |
| Long-running cancellable job | none — all ingest completes in seconds | **delta required** |
| phlo-api + observatory services | `phlo-api` service exists (`profile: api`, mounts project `ro` at `/app` + writable `.phlo/observatory`, `PHLO_AUTHORIZATION_MODE=required`); `observatory` service exists (`web/Dockerfile`, `depends_on: phlo-api`, `PHLO_API_BROWSER_URL`) | covered, added to harness service set |
| Separate ports/volumes/credentials | harness allocates `BundledStackPorts` per boot; `.phlo` env holds generated credentials | covered |
| Seed (failure-injection) commands | `scripts/generate_fixtures.py` + `scripts/run_scenario.py` stage into `generated-data/inbound/` | covered |

### Fixture deltas to land before MC-03/MC-06 acceptance

1. **Multi-segment asset key** — add a dbt model or `phlo_asset_key` meta that
   emits a dotted key (e.g. `marts.batch_summary`) so `/datasets/$datasetId`
   round-trips dots, slashes and encoded names. Track under MC-03.
2. **Long-running cancellable asset** — add a `slow_ingest` asset (e.g. bounded
   `time.sleep` loop checking for termination) excluded from all schedules, so
   MC-06 cancel evidence exercises a genuinely running Dagster execution.
   Track under MC-06.

Both are additive workflows inside the copied fixture project — they do not
change `examples/lakehouses/wap-failure-lab`'s committed scenarios.

## Commands

### Start the disposable stack

```bash
# From the repository checkout. Boots temp project + core services on unique ports.
uv run python - <<'PY'
from phlo_testing.profile_harness import bootstrap_bundled_stack_harness
harness = bootstrap_bundled_stack_harness()          # keep_running via PHLO_KEEP_BUNDLED_STACK
print("project:", harness.project_dir)
print("ports:", harness.ports)
PY
```

To use the failure-lab content instead of the `basic` template, pass
`project_dir` after copying `examples/lakehouses/wap-failure-lab` into the
harness parent dir, or init services inside a copied checkout:

```bash
cp -R examples/lakehouses/wap-failure-lab /tmp/phlo-accept-lab
cd /tmp/phlo-accept-lab
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run phlo services init --force --no-dev   # allocate ports via env overrides
uv run phlo services start --build
uv run phlo doctor
```

Add `phlo-api` and `observatory` to the fixture's service set (`phlo services
add phlo-api observatory` or the profile that carries them) so the same-origin
API path is exercised, not only the providers.

### Seed / drive scenarios

```bash
uv run python scripts/run_scenario.py valid_publish      # healthy promote
uv run python scripts/run_scenario.py quality_failure    # blocking quality failure
uv run python scripts/run_scenario.py retry_recovery     # execution failure + retry
uv run python scripts/run_scenario.py schema_change      # additive schema
uv run python scripts/run_scenario.py concurrent_runs    # overlapping WAP submissions
uv run python scripts/run_scenario.py warning_only       # non-blocking warn evidence
```

### Point phlo-api at the fixture

```bash
export PHLO_PROJECT_PATH=/tmp/phlo-accept-lab           # dir containing phlo.yaml
export PHLO_OBSERVATORY_SETTINGS_BACKEND=postgres        # never memory for control readiness
export PHLO_RUN_EVIDENCE_DB_URL=postgresql://phlo:phlo@127.0.0.1:${POSTGRES_PORT}/phlo
export NESSIE_HOST=127.0.0.1  NESSIE_PORT=${NESSIE_PORT} # NESSIE_URL is ignored
export TRINO_HOST=127.0.0.1   TRINO_PORT=${TRINO_PORT}
export DAGSTER_GRAPHQL_URL=http://127.0.0.1:${DAGSTER_PORT}/graphql
uv run --project packages/phlo-api phlo-api            # or the service's own entrypoint
```

### Inspect

```bash
uv run python scripts/inspect_branches.py --older-than-minutes 60
uv run phlo trino --execute 'SELECT count(*) FROM iceberg.raw.sensor_batches'
uv run phlo catalog tables
```

### Stop and clean up

```bash
harness.cleanup()            # stops services, removes temp project (unless PHLO_KEEP_BUNDLED_STACK=1)
# or, in the copied checkout:
uv run phlo services stop    # stops only this fixture's containers
```

Rules: stop only resources the run created. Never delete a legitimate `.phlo`
directory — it holds WAP reports, run evidence and the observatory state needed
to explain unknown/committed operations. `materialize-check/lakehouse` is
read-only reference.

## Acceptance result format

Every MC-ticket acceptance run emits one record under the repository's
test/artifact conventions (contract tests already gate on
`PHLO_RUN_BUNDLED_STACK_CONTRACT`); store as
`<artifact-root>/observatory-acceptance/<commit-sha>/<scenario>.json` plus
referenced screenshots under `…/screenshots/`.

```json
{
  "schema": "phlo.observatory-acceptance/v1",
  "commit": "<git sha>",
  "config_digest": "<sha256 of sanitized effective env + phlo.yaml>",
  "project_id": "<phlo.yaml name>",
  "fixture": "wap-failure-lab@<git sha>",
  "started_at": "<rfc3339>", "finished_at": "<rfc3339>",
  "scenario": "<name>",
  "ids": {
    "logical_run_id": "...", "orchestrator_run_id": "...",
    "attempt": 1, "operation_id": "...",
    "wap_branch": "...", "candidate_snapshot": "...",
    "release_receipt": "..."
  },
  "checks": [
    {"name": "...", "status": "passed|failed|skipped|blocked", "evidence": "..."}
  ],
  "artifacts": [{"kind": "screenshot|log|report|trino-result", "path": "..."}],
  "unsupported": [{"capability": "...", "reason": "..."}],
  "result": "passed|failed|blocked"
}
```

Never store credentials, raw provider DSNs/URLs, tokens, or secret-bearing
provider output in the record or its artifacts — `config_digest` is computed
over the *sanitized* env.
