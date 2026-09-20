# Tables with Iceberg and Nessie

Files are a useful starting point, but a data product needs more than a directory of outputs. This post explains why table formats and catalogues matter, how Iceberg and Nessie work together in Phlo, and how a branch gives you a safer place to change data before publication.

## The problem from first principles

A collection of files does not tell you which files belong to one table, which schema was current when they were written, or whether a reader saw a complete update. Two processes can also write overlapping files and leave readers with an ambiguous result.

A table format adds structure around the files. It records columns and types, groups files into snapshots, and commits a new table state atomically. Readers can use the table metadata instead of guessing which files to open. The format also gives maintenance tools a way to identify old snapshots and unreferenced files.

Snapshots make time part of the table's meaning. You can ask which state a successful write produced rather than looking at file modification times. Schema metadata makes a column change visible to readers and checks. Atomic commits mean a reader observes the previous state or the new committed state, rather than the middle of a collection of file writes.

Snapshots make time part of the table's meaning. You can ask which state a successful write produced rather than looking at file modification times. Schema metadata makes a column change visible to readers and checks. Atomic commits mean a reader observes the previous state or the new committed state, rather than the middle of a collection of file writes.

A catalogue is the directory for tables. It maps a table name to its current metadata and lets clients find the table without knowing its storage path. A query engine such as Trino asks the catalogue where the table is and then reads the Iceberg metadata and files.

Version control adds a second idea. A code branch lets you prepare a change without changing the main branch. A data branch can do the same for table references. You can write and inspect a candidate table state, compare it with another branch, and merge it when the result is ready.

This is not a claim that data branches remove every risk. A branch still needs an owner, a comparison, and a decision about when to publish. It gives you a place to perform that work with less pressure on the consumer-facing reference. Nessie supplies the reference operation, while Iceberg supplies the table state that the reference identifies.

This is not a claim that data branches remove every risk. A branch still needs an owner, a comparison, and a decision about when to publish. It gives you a place to perform that work with less pressure on the consumer-facing reference. Nessie supplies the reference operation, while Iceberg supplies the table state that the reference identifies.

## How Phlo approaches it

Phlo uses Apache Iceberg for table semantics, MinIO for local object storage, and Nessie for the catalogue and branch references in the default stack. The `phlo-iceberg` package provides table storage. The `phlo-nessie` package provides catalogue operations and the `phlo branch` command group. Trino reads the tables through the `iceberg` catalogue.

Nessie supports refs and promotion. A branch is a named reference to a table state. The write-audit-publish flow uses an isolated branch for a run, audits the result, and promotes the branch only when the checks pass. This is useful when a failed write or quality check must not become the state that consumers read.

The normal data path still runs through Dagster. An asset run writes its table state, quality checks inspect it, and the WAP sensors can promote an audited branch. The branch commands are management and inspection tools. They do not replace `phlo materialize` or `phlo backfill` as the normal asset execution path.

## Try it

List the tables in the tutorial project's catalogue:

```bash
phlo catalog tables
```

List the Nessie branches:

```bash
phlo branch list
```

Create a branch from `main` for an isolated experiment:

```bash
phlo branch create feature/new-model
```

Create one from another reference when you need a different starting point:

```bash
phlo branch create feature/experiment --from dev
```

Compare two branches:

```bash
phlo branch diff feature/new-model main
```

Preview a merge before applying it:

```bash
phlo branch merge feature/new-model main --dry-run
```

The merge command accepts `--no-delete-source` when you need to retain the source branch after the merge. Branch deletion is destructive. Confirm the branch name and its contents before running:

```bash
phlo branch delete feature/new-model
```

Query an Iceberg table through Trino:

```bash
phlo trino --catalog iceberg
```

Then run a query such as:

```sql
SELECT event_id, name, value FROM raw.events;
```

The branch and catalogue commands require the local services to be running. If the catalogue is unavailable, the command reports the service failure rather than proving that a table or branch does not exist.

## Mental model to keep

- Iceberg describes table state and snapshots around object-store files.
- Nessie names and versions catalogue references.
- Trino queries the Iceberg catalogue.
- A branch is a candidate table state, not a second copy of every file.
- WAP separates writing, auditing, and publication.

## Where this goes wrong

- **A table name is correct but the catalogue is unavailable.** `phlo services status` reveals the Nessie or Trino service problem.
- **A reader sees an incomplete update.** Atomic Iceberg commits and WAP promotion keep an audited state separate from a failed candidate.
- **A branch is merged in the wrong direction.** `phlo branch diff` and `--dry-run` show the source and target before the merge.
- **A branch is deleted too early.** The destructive delete command has no recovery promise, so confirm the name and merge status first.
- **A query returns no current rows.** Check the catalogue reference and table history before changing the query.

## Next

Continue with [Orchestration with Dagster](05-orchestration-with-dagster.md) to connect table changes to asset runs and partitions. Read [Write, audit, and publish](../concepts/write-audit-publish.md) for the full promotion model and [Maintain and recover](../guides/maintain-and-recover.md) for branch operations in a runbook.
