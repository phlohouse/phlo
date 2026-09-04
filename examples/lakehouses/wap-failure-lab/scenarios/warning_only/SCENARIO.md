# Scenario: warning_only

## The neutral severity contract, demonstrated

A failed check does not necessarily stop anything. `strict_validation=False`
turns every check on the asset into a WARNING: violations are logged and the
check evaluates as failed, but nothing blocks. The write still lands on a WAP
branch like any launch; what differs is how the promotion sensor treats the
failed check. Under the neutral severity contract (#817), only ERROR-severity
failures block promotion - WARN failures become durable
`passed_with_warnings` evidence and the branch merges.

Blocking versus warning is therefore not "does the check fail" (both fail
loudly) but "what does the failure do to main":

| | strict (`dlt_sensor_batches`) | warning (`dlt_sensor_batches_relaxed`) |
|---|---|---|
| Check fails | run fails on ERROR severity | run succeeds on WARN severity |
| Data path | isolated WAP branch, merged only when checks pass | WAP branch merged with `passed_with_warnings` |
| Main after run | unchanged (ERROR blocked) | advanced (WARN non-blocking) |
| Report | terminal `failed`, `dagster_run_failed` | `promoted`, no failure reason |

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
  the WARN-severity violation did not gate promotion.
- The report ends `status=promoted` with no failure reason: the promotion
  sensor merged the branch because WARN-severity failures are non-blocking
  under the neutral severity contract (#817). The durable aggregate quality
  result records `passed_with_warnings` (severity `warn`, `blocking=false`).
- The `pipeline-run-*` branch is removed after promotion (merge + cleanup).
- Contrast: staging the SAME file for the strict asset fails the run on
  ERROR severity and leaves main untouched (quality_failure semantics).

Use warning assets only when "main advances despite failed warnings" is
acceptable - for this lab, it is the demonstration of the neutral severity
contract, proven live 2026-09-03 (report `9433d990207c434cb9b685a505317f40`).
