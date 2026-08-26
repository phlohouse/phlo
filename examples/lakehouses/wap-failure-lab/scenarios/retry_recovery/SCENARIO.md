# Scenario: retry_recovery

Prove that a transient source outage is survivable: the first attempt fails,
the op retries, and the second attempt promotes normally with the attempt
count recorded as evidence.

## The mechanism

`workflows/retry/transient.py` injects a one-shot failure:

- When armed (arm marker `.phlo/wap-lab/retry-arm`, or `PHLO_WAP_LAB_FAIL_ONCE=1`
  for in-process runs), attempt 1 raises `TransientSourceError`.
- Every attempt appends to the durable counter file
  `.phlo/wap-lab/retry-attempts.txt`.
- The asset declares `max_retries=3`, so Dagster re-executes the op; attempt 2
  succeeds and the run proceeds to promotion.

WAP launches execute inside the Dagster service, so the arm signal is a file
on the shared project filesystem rather than an exported environment variable.

## Steps

```bash
uv run python scripts/run_scenario.py retry_recovery
```

The runner resets the retry state, arms the failure, stages
`batches-2026-08-22.ndjson.gz` (10 clean rows), launches the materialization,
and waits for terminal evidence.

## Expected outcome

- Report reaches `status=promoted`; main gains exactly **10** rows; the branch
  is removed after promotion.
- The counter file reads exactly `2`: two attempts consumed, recovery on the
  second. That file is the run's attempt metadata.
- Without arming (`rm .phlo/wap-lab/retry-arm`), the same batch promotes on
  attempt 1 - rerun after deleting the marker to see the contrast.
