# Evolve a schema

This guide changes a managed table safely by updating its quality schema, inspecting the contract snapshot, planning the migration, and materializing the owner asset.

## Before you start

- You have a running stack and an existing Iceberg table such as `raw.events`.
- The table's owning asset has a Pandera schema under `workflows/schemas/`.
- The project contains a `contracts/` directory created by initialization or a previous contract export.

## 1. Inspect the current schema and contract

Use the schema commands to see the registered quality schema and the table contract before editing Python:

```bash
phlo schema list
phlo schema show events
phlo schema diff events
phlo schema-migrate history raw.events
phlo contracts --help
```

`schema show` prints fields and constraints, while migration history and contract commands show the storage-facing record against which a new materialization is compared.

A contract snapshot is a versioned description of the table's expected columns, types, and compatibility metadata. Phlo stores project snapshots under `contracts/` so a schema change can be reviewed independently of the running table.

## 2. Add a nullable column

Add the field to the Pandera model first when existing rows do not contain a value:

```python
import pandera.pandas as pa


class EventSchema(pa.DataFrameModel):
    event_id: str
    name: str
    value: int = pa.Field(ge=0)
    source: str | None = pa.Field(nullable=True)
```

The new field is accepted for incoming batches without requiring historical rows to be rewritten, and the next migration plan identifies the corresponding table addition.

## 3. Plan and apply the addition

Generate a plan before mutating the table:

```bash
phlo schema validate workflows/schemas/events.py
phlo schema-migrate diff raw.events
phlo schema-migrate plan raw.events
phlo schema-migrate apply raw.events
```

`diff` reports the pending quality-to-storage change, `plan` records the intended operation, and `apply` performs the provider-owned migration after confirmation. In automation, add `--non-interactive` and treat a required confirmation as a failure.

| Command | Use |
| --- | --- |
| `phlo schema generate` | Generate a Pandera schema from a bounded DLT inference sample. |
| `phlo schema-migrate scaffold-yaml` | Write migration scaffold YAML from a schema change. |
| `phlo schema-migrate export-contract` | Export a current contract snapshot for a table. |
| `phlo schema-migrate history` | Show applied schema versions and migration history. |

## 4. Rename a column as an explicit migration

A rename is not the same operation as adding a new field. Preserve the old field until consumers are migrated, or create a migration scaffold that records the provider-supported rename explicitly:

```bash
phlo schema-migrate scaffold-yaml raw.events --output .phlo/migrations/events-rename.yaml
phlo schema-migrate plan raw.events --migration-file .phlo/migrations/events-rename.yaml
phlo schema-migrate apply raw.events --migration-file .phlo/migrations/events-rename.yaml --yes
```

The scaffold makes the old and new names reviewable. The plan output is the visible proof that Phlo recognized a rename rather than silently dropping a column.

## 5. Materialize without refreshing the contract

Materialization refreshes contracts automatically unless you opt out. Use the flag when reviewing a run against a previously approved snapshot:

```bash
phlo materialize dlt_events --partition 2025-01-15 --no-contract-refresh
```

The asset runs with the existing contract context and does not overwrite the snapshot as a side effect. Omit the flag only when the schema change has passed review and should become the new contract state.

## Verify

```bash
phlo catalog describe raw.events
phlo schema-migrate history raw.events
```

The `describe` output contains the new column, and migration history shows the applied version or plan outcome. If the migration is rejected, keep the old contract and use the reported compatibility reason to revise the schema.

## Related

- [Ingest data](ingest-data.md) for the schema that drives an ingestion table.
- [Project layout](../reference/project-layout.md) for `contracts/` and workflow paths.
- [Errors](../reference/errors.md) for schema and table error codes.
