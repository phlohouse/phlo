# Scenario: quality_failure

Prove fail-closed: a strict run whose batch breaks the contract must leave
main byte-for-byte unchanged, retain a reproducible report, and keep the
violating branch alive for audit.

## The data

Two fixtures share partition 2026-08-20; each breaks exactly ONE invariant
(proven by tests), and staging both together exercises two check families in
one failed run:

- `batches_null_reading-2026-08-20.ndjson.gz`: row `b-1004` has a null
  `reading_value` - breaks the Pandera not-null contract (blocking).
- `batches_duplicate_batch_id-2026-08-20.ndjson.gz`: `b-2003` appears twice -
  breaks the blocking `assert_batch_ids_unique` domain check.

## Steps

```bash
uv run python scripts/run_scenario.py quality_failure
```

The runner stages both files, launches the same materialization as
valid_publish, waits for non-promotion evidence, then asserts catalog and
Trino state.

## Expected outcome

- The Dagster run FAILS on the first violated check (strict validation raises;
  Dagster's op retry policy re-runs it up to max_retries=3, and every attempt
  fails identically).
- `iceberg.raw.sensor_batches` count is unchanged and the `main` ref hash is
  identical before/after: nothing leaked.
- A WAP report exists with `schema_version=phlo.wap_report.v2`, a recorded
  `dagster_run_id`, and NO promoted status.
- **Platform reality (live-proven 2026-09-03):** the auto-promotion sensor
  scans SUCCESS, FAILURE, and CANCELED runs and transitions the failed run's
  report to terminal `status="failed"` with
  `failure_reason="dagster_run_failed"`. The runner observes the terminal
  `failed` classification; "main unchanged + branch retained" remains the
  data-integrity signature.
- The violating branch (`pipeline-run-*`) is STILL PRESENT for audit - retained
  refs are the audit trail. List it via:
  ```bash
  uv run python scripts/inspect_branches.py --older-than-minutes 0
  ```
- Re-running the scenario reproduces the same evidence under a new run id.
