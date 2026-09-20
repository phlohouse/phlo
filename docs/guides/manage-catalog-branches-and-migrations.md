# Manage catalog branches and migrations

Use this runbook to isolate catalog changes on a Nessie branch and execute a reviewed data migration.

## Before you start

- Run the commands from the project root.
- Confirm the source and target references.
- Create and verify a backup before a destructive migration.

## 1. Create and inspect a branch

```bash
phlo branch create feature/new-model
phlo branch list
phlo branch diff feature/new-model main
```

## 2. Preview a merge

```bash
phlo branch merge feature/new-model main --dry-run
```

Review the diff before running the merge without `--dry-run`.

## 3. Delete a branch only after review

Before deletion, confirm the branch name, head, and merge status:

```bash
phlo branch delete feature/new-model
```

Branch deletion is destructive. The command prevents deletion of a non-empty branch unless you pass `--force`.

## 4. Validate a migration

```bash
phlo migrate list
phlo migrate validate migrations/example.yaml
phlo migrate run migrations/example.yaml --dry-run
phlo migrate status
```

Confirm the migration spec, target, backup, and approval before execution. `phlo migrate` does not provide an automatic rollback.

## 5. Execute and verify the migration

```bash
phlo migrate run migrations/example.yaml
phlo migrate status
```

Confirm the migration status and query the affected tables before allowing dependent runs to continue.
