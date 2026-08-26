# Scenario: warning_only

## THIS IS THE LESSON OF THE WHOLE LAB - READ IT TWICE

A failed check does not necessarily stop anything. `strict_validation=False`
turns every check on the asset into a WARNING: violations are logged and the
check evaluates as failed, but nothing blocks. And because non-strict write
branch resolution skips WAP isolation entirely, **the rows are written
straight to main during the run** - before any promotion decision exists.
Main advances while the WAP report simultaneously records a blocked promotion.

Blocking versus warning is therefore not "does the check fail" (both fail
loudly) but "what does the failure do to main":

| | strict (`dlt_sensor_batches`) | warning (`dlt_sensor_batches_relaxed`) |
|---|---|---|
| Check fails | run fails, branch retained | run succeeds |
| Data path | isolated WAP branch, never merged | written directly to main |
| Main after run | unchanged | advanced |
| Report | stays `launched` (see gap 1) | `promotion_blocked`, `asset_checks_failed` |

## The data

`batches_stale-2026-08-24.ndjson.gz`: 7 rows recorded 2026-08-01 against
partition 2026-08-24 - 23 days stale, far outside the 7-day window of
`assert_recordings_near_partition`. The Pandera contract passes; only the
non-blocking domain check fails.

## Steps

```bash
uv run python scripts/run_scenario.py warning_only
```

## Expected outcome

- The relaxed run SUCCEEDS despite its failed check.
- `iceberg.raw.sensor_batches_relaxed` gains exactly **7** rows on main:
  the violation did not gate publication.
- The report ends `status=promotion_blocked` with
  `failure_reason=asset_checks_failed`: the promotion sensor refused to merge
  the (already empty) branch because an asset check failed - even though that
  same check was declared non-blocking by the asset itself.
- A residual empty `pipeline-run-*` branch remains until retention cleanup;
  inspect it with:
  ```bash
  uv run python scripts/inspect_branches.py --older-than-minutes 0
  ```
- Contrast: staging the SAME file for the strict asset fails the run and
  leaves main untouched (quality_failure semantics).

Use warning assets only when "main already has the bad rows" is acceptable -
for this lab, it is the demonstration.
