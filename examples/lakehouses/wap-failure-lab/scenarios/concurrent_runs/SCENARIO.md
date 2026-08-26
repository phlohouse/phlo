# Scenario: concurrent_runs

Prove branch isolation under back-to-back launches: two partitions run
serially through the same strict asset, each on its own WAP branch, and both
promote without cross-contamination.

## The data

- `partition_a-2026-08-20.ndjson.gz`: 12 rows, sensors s-101..s-104,
  batch ids b-6001..b-6012.
- `partition_b-2026-08-21.ndjson.gz`: 8 rows, sensors s-201..s-204,
  batch ids b-7001..b-7008.

Batch ids and sensors are disjoint by construction (proven container-free in
tests), so any row appearing on the wrong partition would be contamination.

## Steps

```bash
uv run python scripts/run_scenario.py concurrent_runs
```

The runner launches partition A, waits for its promotion, then immediately
launches partition B - no manual reset between them.

## Expected outcome

- Both reports reach `status=promoted`; each names a DIFFERENT
  `pipeline-run-*` branch.
- Per-partition counts are exact multiples of the batch sizes: partition
  2026-08-20 holds a multiple of **12** rows, 2026-08-21 a multiple of **8**;
  total row delta across the two runs is exactly **20** on a fresh catalog.
- Both branches are removed after their promotions: serial promotion leaves
  no residual refs. Check with:
  ```bash
  uv run python scripts/inspect_branches.py --older-than-minutes 0
  ```
