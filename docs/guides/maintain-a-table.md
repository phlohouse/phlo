# Maintain an Iceberg table

Use this runbook to compact a table or expire snapshots with a bound, reviewable plan.

## Before you start

- Run the commands from the project root.
- Confirm the table name and Nessie reference.
- Ensure that your operator identity can authorise `operations.maintenance.apply`.
- Choose safety limits that fit the table before expiring snapshots.

## 1. Inspect the maintenance inventory

```bash
phlo operations maintenance inventory
```

Confirm that the target table and its current maintenance state appear in the output.

## 2. Save a plan

For compaction, save the read-only plan to a file:

```bash
phlo operations maintenance plan \
  --operation compact \
  --table <catalog.schema.table> \
  --format json > .phlo/maintenance-plan.json
```

For snapshot expiry, set explicit safety limits:

```bash
phlo operations maintenance plan \
  --operation snapshot_expiry \
  --table <catalog.schema.table> \
  --max-affected-objects <count> \
  --max-affected-bytes <bytes> \
  --format json > .phlo/maintenance-plan.json
```

Review the operation, table, reference, affected objects, affected bytes, and `plan_token` in the saved JSON. The plan step does not change the table.

## 3. Apply the current plan

Apply only the reviewed plan. Replace `<plan-token>` with the exact `plan_token` from the file:

```bash
phlo operations maintenance apply \
  --plan .phlo/maintenance-plan.json \
  --confirmation-token <plan-token>
```

The command rejects a changed table or a token that does not match the plan.

## Verify the result

```bash
phlo catalog history <schema.table>
phlo operations maintenance inventory
```

Confirm that the catalog history contains the expected maintenance result and that the table remains readable.
