# Scenario: valid_publish

Prove the happy path: a clean batch lands on a WAP branch, every check passes,
and promotion merges it to main atomically and removes the branch.

## Steps

1. Generate fixtures and start the platform:
   ```bash
   uv run python scripts/generate_fixtures.py
   uv run phlo services init --force --no-dev && uv run phlo services start --build
   ```
2. Run the scenario:
   ```bash
   uv run python scripts/run_scenario.py valid_publish
   ```

The runner stages `generated-data/scenarios/valid_publish/batches-2026-08-20.ndjson.gz`
into `generated-data/inbound/`, launches `phlo materialize dlt_sensor_batches --partition 2026-08-20`,
then polls `.phlo/wap-reports/` for terminal evidence.

## Expected outcome

- The WAP report (`schema_version` `phlo.wap_report.v2`) reaches `status=promoted`
  with `target_hash_after != target_hash_before`: main moved exactly once.
- Row count on `iceberg.raw.sensor_batches` grows by exactly **12**
  (sensors s-001..s-004, three batches each).
- The `pipeline-run-*` branch is **gone** after promotion: cleanup removed it.
- Downstream `batch_summary` sees the new rows only because they reached main;
  rebuilding it yields batch_count 3 per sensor.

Failure at any check instead would leave main untouched - that is the
quality_failure scenario's job.
