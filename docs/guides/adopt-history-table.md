# Adopt an existing history table

Use the `phlo_iceberg.history_adoption` Python API to validate an existing append-only Iceberg table before native history ingestion. Ordinary history writes continue to reject unmarked nonempty tables. This procedure never rewrites identities, removes duplicates, or repairs conflicting payloads.

## Check the old-to-native contract

Before adoption, compare the legacy writer with the [native history contract](../reference/python-api.md#immutable-history-mode).

- Select the existing entity and immutable version columns. Versions are scoped to their entity, not globally unique or ordered by arrival time.
- Select either explicit payload columns or a supplied payload hash column. Exclude arrival metadata. Native value comparison treats null as equal to null and floating-point NaN as equal to NaN, including nested values. It does not normalise Unicode, JSON text, timestamps, case, or whitespace beyond the stored Iceberg types.
- For a supplied hash, retain the legacy algorithm, encoding, canonicalisation, null handling, and collision policy in your migration record. The validator checks nonempty string or binary values and equality for each identity pair. It cannot infer the algorithm or prove that a stored hash describes the raw payload. Native ingestion trusts that hash too. If provenance is unavailable, compare explicit payload columns or rebuild from a trusted source rather than claiming digest compatibility.
- Check the report's full schema and policy definition, including Iceberg field IDs and types. Future batches must satisfy the target schema and supply every identity and comparison column.
- Review identical duplicate versions. Adoption retains every physical row, including its original metadata. A later identical replay skips the version without removing old duplicates. Different payloads for the same pair block adoption.

## Validate without changing the table

Run this example from your configured Phlo project environment with `phlo-iceberg` installed. Replace the table, columns, and branch with your migration target.

```python
import json
from pathlib import Path

from phlo.capabilities.history import HistoryPolicy
from phlo_iceberg.history_adoption import validate_history_adoption

policy = HistoryPolicy("entity_id", "version_id", ("payload",))
report = validate_history_adoption("raw.events", policy, ref="migration")
Path("history-validation.json").write_text(
	json.dumps(report, indent=2), encoding="utf-8"
)
print(report["compatible"], report["rows_scanned"], report["issues"])
```

Expect `compatible=True` only when all selected schema and row checks pass. An empty unmarked table is compatible if its schema is valid. Missing columns, nonscalar identity types, invalid identity or hash values, incomplete policy markers, and conflicting immutable payloads produce an incompatible report. Read or catalogue failures raise an exception, never a successful partial report.

The report contains the table UUID, catalogue ref, metadata location, pinned snapshot ID, schema, canonical policy properties, scanned row count, distinct valid identity count, identical duplicate count, and every issue. Row issues contain one-based scan ordinals and a reason naming the invalid column or the first row with a different payload. These ordinals identify rows within that scan, not stable file row IDs. Keep the report with its pinned snapshot and retain that snapshot while investigating. Schema errors prevent a row scan.

Validation reads every Arrow batch of the pinned snapshot, projects only selected fields, and stores the identity and payload comparison index in a private temporary SQLite database. It does not load the entire table into memory or stop after the first page. Allow local temporary disk space proportional to distinct versions and selected payload size. The current Arrow batch and the diagnostic list remain in memory; a table with many invalid rows can produce a large report. Scratch files are removed on normal completion or exception. After process termination, remove leftover `phlo-history-*` directories from the process's temporary directory only after confirming that no validator is using them. Scratch files contain selected source values and require the same access controls as the table.

## Adopt the reviewed revision

Before this step, stop legacy writers, schema changes, rollbacks, policy edits, and maintenance on the target ref. Arrange a recoverable catalogue backup and retain the source snapshot and metadata. Do not modify a protected `main` ref directly. Use a branch and the normal governed merge process where main protection applies.

After reviewing the report and the legacy comparison contract, run the explicit operation. Reuse `policy` and `report` from the previous example. Use a unique operation ID and record it before the call.

```python
from phlo_iceberg.history_adoption import adopt_history_table

receipt = adopt_history_table(
	"raw.events",
	policy,
	ref="migration",
	expected_metadata_location=report["metadata_location"],
	operation_id="events-history-adoption-2026-10-19",
	operator="data-platform",
	reason="Replace the legacy append-only writer after contract review",
)
Path("history-adoption.json").write_text(
	json.dumps(receipt, indent=2), encoding="utf-8"
)
```

Adoption reloads the reviewed revision and repeats the complete validation. A report is not permission to stamp metadata. A changed metadata location requires another dry run and review. The transaction asserts the table UUID, current schema ID, and original snapshot head, including an absent head for an empty table. A definite commit conflict fails without retrying against unreviewed data.

The transaction publishes `phlo.history.policy`, its SHA-256 fingerprint, and a JSON audit receipt in `phlo.history.adoption`. It also creates a data-preserving append snapshot whose summary carries `phlo.history.adoption.id`. That snapshot reuses the existing data manifests and adds no data files. Advancing the snapshot head makes competing adoption transactions contend even when they choose different policies. Adoption does not support rebinding an existing native policy.

The receipt records the operator, reason, operation ID, validation timestamp, table UUID, source metadata location, before and after snapshot IDs, policy fingerprint, and validation counts. Optional `evidence_context={"project_id": ..., "run_id": ...}` emits normal mutation evidence. The catalogue receipt persists atomically even without a run context or if evidence transport fails. Keep the full dry-run report separately.

Property-only edits by noncooperating external clients cannot be guarded by the snapshot assertion. Quiescing those clients is mandatory. Ordinary append, overwrite, merge, and independent refs do not become history-safe after adoption.

## Verify before switching writers

1. Read the table again and compare all original rows, schema field IDs, and data file inventory with the retained source snapshot. Only the adoption snapshot and properties should change.
2. Replay a known version through `IcebergResource.history_parquet` with the adopted `HistoryPolicy`. Expect `rows_inserted=0` and `rows_skipped` equal to the incoming row count, with no new snapshot or metadata revision.
3. On an isolated validation branch, append a new version and verify that exactly one row appears. Submit a different payload for an existing pair and verify `HistoryConflictError` with no table change.
4. Switch only cooperating native history writers to the adopted table. Keep the backup and migration records until your recovery window ends.

## Recover an interrupted adoption

Keep writers stopped until the outcome is known. Do not remove policy properties or rerun under a different operation ID to bypass a failure.

- If validation fails or the process stops before commit, the table remains unmarked. Rerun the dry run and inspect the unchanged source revision before adopting again.
- For `CommitFailedException` or a changed-revision error, review the competing update and run validation against the new revision. No automatic retry occurs.
- For a lost commit response, `HistoryCommitUnknownError` carries empty mutation counts and `reconciliation`. `adoption_record_matches` compares the exact attempted receipt and `policy_matches` checks its fingerprint. An unavailable readback is not proof that the commit failed.
- Reload the catalogue and inspect `phlo.history.adoption`, both policy properties, and the snapshot carrying the operation ID. If they match, repeating the same operation ID, policy, and original `expected_metadata_location` returns the durable receipt without another commit. If the operation did not land and the source revision is unchanged, the same explicit call revalidates and adopts it. A different receipt or policy requires investigation, not overwriting.

A hard process termination cannot return reconciliation. Use the recorded operation ID for the same readback procedure. An uncommitted attempt can leave orphan manifest-list metadata; use the normal retention-safe orphan cleanup after establishing the commit outcome. Rolling back a snapshot alone does not remove policy properties or the audit record. Do not use rollback as an adoption undo operation.

## Copy or rebuild an incompatible table

Do not stamp policy metadata on an incompatible table. Keep the original unchanged and use a new table on an isolated migration ref.

1. Export the pinned source snapshot and its schema. Save the incompatible report and catalogue backup.
2. Resolve each reported schema or row defect against the authoritative source. For conflicting versions, determine the correct immutable payload from source evidence. If the original pair cannot represent both events faithfully, define a separately reviewed domain migration, not an adoption-time identity rewrite. Preserve unresolved and superseded source records in the migration evidence.
3. Create a new empty Iceberg table with the reviewed schema using `IcebergResource.ensure_table`. Stage the corrected source records as Parquet. Do not silently cast identities, deduplicate source rows, or discard conflicts to make validation pass.
4. Validate the complete staged history under the chosen policy before publishing chunks. If the staged data is too large for one native history batch, copy all records with ordinary append into the isolated, unmarked destination and run `validate_history_adoption` over that entire table. Native history ingestion materialises each incoming batch in memory; keep each staging batch bounded.
5. Compare the copied row multiset, identities, payloads, and file readability against the reviewed corrected export. Account for every original row, including retained duplicates and any explicitly adjudicated corrections. Keep the source snapshot and correction evidence. A matching row count alone does not prove a correct copy.
6. Adopt only after the complete destination report is compatible. Follow the replay, new-version, conflict, and interrupted-operation checks above on the destination.
7. Cut consumers and native writers over through your normal reviewed catalogue or deployment procedure. Keep the old table read-only for recovery. This API does not perform cutover, delete the source, or authorise production changes.
