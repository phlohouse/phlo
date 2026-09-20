# Recover a Dagster run

Use this runbook to terminate a harmful run or re-execute a failed run.

## Before you start

- Record the run identifier, asset, partition, failed step, and first error.
- Confirm that replaying the affected steps is safe.
- Protect downstream consumers if the run may have published incomplete data.

## Recover through Dagster

1. Open the failed run in the Dagster UI.
2. To retry it, select **Re-execute** and choose all steps or the steps from the failure.
3. To stop an active run, select **Terminate**.
4. Confirm that the replacement run finishes and that its asset checks pass.

The Dagster webserver authorises these GraphQL actions as `run.manage` operations.

## Recover through the Phlo API

The Phlo API exposes these routes:

- `POST /api/observatory/runs/{run_id}/retry`
- `POST /api/observatory/runs/{run_id}/cancel`

Both routes require the `lakehouse:operate` permission, a non-blank idempotency key, and explicit confirmation.

## Verify recovery

Confirm the final run state, asset checks, catalog snapshot, and consumer-facing result. Keep the original and replacement run identifiers, relevant logs, action taken, and verification result with the incident record.
