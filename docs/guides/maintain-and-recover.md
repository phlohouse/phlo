# Maintain and recover a deployment

Use the runbook that matches the state you need to change or recover:

- [Maintain an Iceberg table](maintain-a-table.md) creates and applies a bound compaction or snapshot-expiry plan.
- [Create and verify a backup](create-and-verify-a-backup.md) captures the state owned by the v1 backup providers and verifies its manifest.
- [Manage catalog branches and migrations](manage-catalog-branches-and-migrations.md) isolates catalog changes and executes reviewed migration specs.
- [Recover a Dagster run](recover-a-dagster-run.md) re-executes or terminates a run through Dagster or the Phlo API.

## Restore and upgrade boundary

Phlo does not currently provide a live deployment restore or upgrade operation. The `phlo operations restore apply` and `phlo operations upgrade apply` commands require `--fixture-substrate`. They stage files or version markers for contract testing and explicitly do not mutate a live deployment.

Do not use those commands as an operational recovery path. Define and test provider-specific restore and upgrade procedures before running Phlo in production.

## Related

- [Write-audit-publish](../concepts/write-audit-publish.md)
- [Evidence, audit, and compliance](../concepts/evidence-audit-and-compliance.md)
- [Prepare a Compose deployment for production](run-in-production.md#release-artifact-support-boundary)
