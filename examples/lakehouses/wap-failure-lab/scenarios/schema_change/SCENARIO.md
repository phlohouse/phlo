# Scenario: schema_change

Prove additive schema evolution: a batch carrying a new optional column
promotes, its score column is available, and pre-change rows are
untouched (visible to old readers with NULL in the new field).

## The data

`batches-2026-08-23.ndjson.gz` holds 8 clean rows for sensors s-401..s-404
where every row also carries `reading_quality_score` (55.0..89.0). The
contract declares it as `optional + nullable`, so:

- pre-change batches (no such column) still validate against the SAME model;
- DLT keeps the column because it is declared (undeclared columns are dropped);
- the Iceberg table grows additively - no rewrite, no destructive migration.

## Steps

Run valid_publish first to establish rows without score values:

```bash
uv run python scripts/run_scenario.py valid_publish
uv run python scripts/run_scenario.py schema_change
```

## Expected outcome

- Report reaches `status=promoted`; partition 2026-08-23 holds **8** rows.
- `reading_quality_score` is present and all **8** new rows have scores.
  Column count may grow by zero or one because the optional field can be
  created with the initial table schema.
- Every row recorded BEFORE this run has `reading_quality_score IS NULL`
  (count of old rows equals count of NULL-score rows): old readers see the
  same data plus one nullable column.
- Run once per fresh catalog: repeating the fixture re-appends the
  same batch ids under append semantics, doubling the partition like any raw
  replay.
