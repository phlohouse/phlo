# Maintain and recover a deployment

Use this runbook for plan-first table maintenance, backups, restores, upgrades, catalog branches, data migrations, and Dagster run recovery.

## Before you start

Run these commands from the project root with the service credentials and permissions required by the selected operation. Use `--json` when an automation system consumes the result, and use `--non-interactive` when prompts must fail closed.

## 1. Plan table maintenance

Create a read-only maintenance plan before applying any table changes.

```bash
phlo operations maintenance plan --operation compact --table <catalog.schema.table>
phlo operations maintenance inventory
```

The maintenance command covers compaction and snapshot expiry. Apply only the exact current plan with `phlo operations maintenance apply`.

## 2. Create and verify a backup

Create a verified backup set before a restore or upgrade.

```bash
phlo operations backup create --target <new-empty-directory>
phlo operations backup verify --backup-set <backup-set-directory>
```

The backup implementation records a manifest and package versions. Verify the set independently before using it as an input to another plan.

## 3. Restore an explicit target

Before restoring, stop writes to the target, confirm the backup set and target path, and ensure that the target is not the backup source. Restore replaces state and cannot be treated as a read-only inspection.

```bash
phlo operations restore plan --backup-set <backup-set-directory> --target <new-empty-target>
phlo operations restore apply --plan <restore-plan.json> --confirmation-token <plan-token>
```

The apply operation is bound to the verified backup digest and explicit target.

## 4. Prove an upgrade

Create an upgrade plan only after verifying a compatible backup.

```bash
phlo operations upgrade plan --backup-set <backup-set-directory> --target <target-directory> --from <from-version> --to <to-version>
phlo operations upgrade apply --plan <upgrade-plan.json> --confirmation-token <plan-token>
```

The upgrade implementation validates the supported version pair, binds the plan to the backup digest, and records provider reconciliation evidence. Do not use an operations upgrade as a substitute for `phlo migrate`.

## 5. Manage catalog branches

Create and inspect a Nessie branch before making isolated catalog changes.

```bash
phlo branch create feature/new-model
phlo branch list
phlo branch diff feature/new-model main
phlo branch merge feature/new-model main --dry-run
```

Before deleting a branch, confirm its name, head, and merge status. Branch deletion is destructive, and `phlo branch delete` prevents deletion of a non-empty branch unless you pass `--force`.

```bash
phlo branch delete feature/new-model
```

WAP uses an isolated branch or snapshot strategy with Nessie. The WAP sensors promote successful audited work and clean up stale owned branches.

## 6. Plan a data migration

List and validate a migration spec before executing it.

```bash
phlo migrate list
phlo migrate validate migrations/example.yaml
phlo migrate run migrations/example.yaml --dry-run
phlo migrate status
```

`phlo migrate` executes data migration specs. `phlo operations upgrade` proves and applies a supported deployment upgrade, so keep the two workflows separate.

Before executing a migration, confirm the spec, target, backup, and approval. Migration execution writes data and is not reversible through this command.

```bash
phlo migrate run migrations/example.yaml
```

The `phlo migrate decorators-2026-05` codemod reports pending decorator changes by default. Use `--check` for CI or `--diff` to inspect changes. Before using `--write`, review the diff and commit a recovery point.

## 7. Recover a Dagster run

Open the Dagster UI and select the failed run. Select **Re-execute** and choose all steps or the steps from the failure, or select **Terminate** to cancel a running run.

The Dagster webserver authorises these GraphQL actions as `run.manage` operations. The Phlo API also exposes `POST /api/observatory/runs/{run_id}/retry` and `POST /api/observatory/runs/{run_id}/cancel`. These API routes require the `lakehouse:operate` permission and a non-blank idempotency key. The run-action contract marks both actions as requiring confirmation.

## Verify

Verify the operation result in its command output and in the Dagster UI or catalog view that owns the changed state. Keep the plan, operation identifier, backup digest, and reconciliation result with the incident record.

## Related

- [Write-audit-publish](../concepts/write-audit-publish.md)
- [Evidence, audit, and compliance](../concepts/evidence-audit-and-compliance.md)
- [Run in production](run-in-production.md#release-artifact-support-boundary)
