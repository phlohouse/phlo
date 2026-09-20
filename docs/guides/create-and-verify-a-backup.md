# Create and verify a backup

Use this runbook to create one backup set from the registered v1 backup providers and verify its manifest and digests.

## Before you start

- Run the commands from the project root.
- Choose a new, empty target directory.
- Ensure that your operator identity can authorise `operations.backup.create`.
- Quiesce writes when your provider-specific consistency requirements demand it.

## 1. Create the backup set

```bash
phlo operations backup create --target <new-empty-directory>
```

The command finalises the manifest only after every registered contributor succeeds. A failed run leaves no accepted backup set.

## 2. Verify the backup independently

```bash
phlo operations backup verify --backup-set <backup-set-directory>
```

To reject a backup from another deployment, add `--expected-deployment <deployment-id>`.

Verification is read-only. It rejects partial, corrupt, mixed-run, or wrong-owner sets.

## 3. Retain the evidence

Record the backup-set identifier, manifest, package versions, artifact digests, source deployment, and verification result with the deployment record.

## Restore boundary

The current CLI can verify these backup artifacts, but it cannot restore a live deployment. `phlo operations restore apply` operates only on a fixture substrate. Keep a separately tested restore procedure for PostgreSQL, object storage, the catalog, and any other stateful provider.
