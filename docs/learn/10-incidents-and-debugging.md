# Incidents and debugging

An incident is a gap between the result people need and the result the system can currently provide. This post gives you a calm sequence for detecting, triaging, stabilising, fixing, and learning from a failure in a Phlo project.

## The problem from first principles

The first minutes of an incident are full of incomplete information. Someone sees a stale dashboard, a failed run, or an alert. Several explanations are possible, and changing multiple things at once can destroy the evidence that would identify the cause.

A useful incident flow has five stages:

1. Detect the symptom and record its scope.
2. Triage the signal that is closest to the failure.
3. Stabilise the consumer or stop further damage.
4. Fix the cause and replay the smallest safe unit.
5. Learn by recording the cause, evidence, and follow-up action.

The goal is not to prove who made a mistake. The goal is to restore a trustworthy result while preserving enough context to prevent a repeat.

The first five minutes should answer four questions. Which asset and partition are affected? Did the run fail, remain active, or finish with a failed check? Is the problem limited to one service or visible across the stack? What must be protected while you investigate? Write the answers down before you try a fix.

## How Phlo approaches it

Dagster is the starting point because every normal asset run has a status, partition, step log, and check result. The Phlo CLI then provides supporting views. `phlo logs --service` reads service logs. `phlo services status` shows the container state. `phlo status` shows asset and service summaries.

Recovery should match the failure boundary. Re-execute from failure when the upstream work is valid and only a later step failed. Terminate a running Dagster run when it is stuck or causing harm. Retry a partition through `phlo materialize` when you know the partition is safe to replay.

Nessie branches provide a rollback boundary for table references. A branch-first workflow can keep a candidate state away from `main`. The maintenance guide documents concrete restore, branch, and migration operations. Treat those as guarded changes rather than improvising during an incident.

An incident record should contain the first symptom, affected asset and partition, the Dagster run, the failed step, the relevant logs, the action taken, and the verification that consumers recovered. Run evidence and audit records can preserve that account for later review.

A root-cause note can stay short. Record the symptom, trigger, contributing condition, missing detection, immediate fix, permanent fix, and owner. A blameless debrief asks which system conditions made the incident possible and which follow-up will make the next incident easier to detect or contain.

A good debrief leaves the system easier to operate. It may add a quality check, improve a log message, change a partition boundary, document a recovery action, or assign an owner for a stale alert. The action should connect to evidence from the incident rather than to a general desire to add more tooling.

Keep recovery commands and destructive actions separate in the incident notes. A retry may be safe because the partition is idempotent. A branch merge or restore changes the consumer-facing state and needs a second review. Writing that distinction down helps the next operator act quickly without treating every action as equally reversible.

## Try it

Start with the Dagster UI:

```text
http://localhost:10006
```

Open **Runs** and select the failed run. Read the failed step log and note the asset, partition, and error before changing anything.

Read the corresponding service log:

```bash
phlo logs --service dagster --tail 100
```

Check the infrastructure:

```bash
phlo services status
```

Check the broader state:

```bash
phlo status --assets
phlo status --services
```

If the failure is limited to one step and its inputs are valid, use **Re-execute** in Dagster and choose **From failure**. If a run is still active and must stop, select **Terminate**. Use the API run-action routes when an authorised integration needs to perform the action:

```text
POST /api/observatory/runs/{run_id}/retry
POST /api/observatory/runs/{run_id}/cancel
```

The API routes require `lakehouse:operate` and a non-blank idempotency key. The action contract marks them as requiring confirmation.

If a table reference must be isolated, create a branch and inspect its difference:

```bash
phlo branch create incident/recovery
phlo branch diff incident/recovery main
```

Do not delete a branch or apply a restore during the first triage pass. Confirm the target, evidence, and recovery plan immediately before any destructive action.

## Mental model to keep

- Preserve evidence before changing state.
- Start with the smallest signal that identifies the failure.
- Stabilise consumers before optimising the permanent fix.
- Replay the smallest safe partition or step.
- Treat rollback and restore as separate, reviewed operations.
- A blameless review asks what made the failure likely and hard to see.

## Where this goes wrong

- **Several fixes are applied at once.** The original cause becomes hard to distinguish from the changes.
- **A failed step is rerun from the beginning.** Re-execute from failure when its inputs are still valid.
- **A direct provider command is used as recovery.** It can change data without the normal Dagster record, so prefer Dagster actions.
- **A branch is deleted during triage.** Branch deletion is destructive and should wait until its contents and recovery value are confirmed.
- **The incident is closed when the process is green.** Verify the partition, check result, freshness, and consumer query before declaring recovery.

## Next

Continue with [Performance and cost](11-performance-and-cost.md) to improve a healthy system without guessing. Keep [Monitor and debug](../guides/monitor-and-debug.md) and [Maintain and recover](../guides/maintain-and-recover.md) nearby during an incident.
