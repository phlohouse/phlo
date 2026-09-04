# ADR 0037: Advanced Reconciliation Checks

## Status

**Proposed**

## Context

Phlo currently ships reconciliation checks that compare row counts and aggregates between source
and target tables. These catch bulk loss or aggregation drift, but they do not detect silent
row-level mismatches when counts and aggregates still align (for example, incorrect values for a
subset of rows). Users can build checksum comparisons with ad-hoc SQL, but this is repetitive and
hard to standardize across pipelines.

We want a first-class, warehouse-native reconciliation check that detects row-level drift while
remaining deterministic, partition-aware, and cost-controlled.

## Decision

Add a checksum-based reconciliation check to `phlo_quality.reconciliation` that compares
row-level hashes between a source and target table using Trino, and add three complementary
reconciliation capabilities that were prioritized alongside checksums: key-level anti-join
parity, absolute-difference tolerances, and multi-aggregate parity.

### Check definition

Introduce `ChecksumReconciliationCheck` with the following behavior:

- **Required inputs**:
  - `source_table`: fully qualified source table name.
  - `key_columns`: primary key or composite key used to align rows across tables.
- **Optional inputs**:
  - `columns`: columns to hash. If omitted, hash all non-metadata columns from the target table.
  - `partition_column`: used to filter both source and target to the same partition.
  - `tolerance`: allowed fraction of mismatches (default 0.0).
  - `sample`: optional sampling fraction for large tables (default none).
  - `limit`: optional cap on rows hashed for cost control.
  - `hash_algorithm`: `xxhash64` by default, with `md5` supported when configured.

### Hashing semantics

- Hashes are computed over a **stable, ordered** list of columns.
- Each value is normalized before hashing:
  - `NULL` becomes a sentinel value (`'__NULL__'`).
  - Timestamps are normalized to UTC ISO8601 strings.
  - Floats are rounded to a configurable precision (default 6) before casting.
- The hash input is the concatenation of normalized values with a delimiter.

### Comparison logic

- The check computes a `(key_columns, row_hash)` dataset for both source and target.
- It joins by `key_columns` and reports:
  - missing keys (in target but not in source, and vice versa),
  - hash mismatches for shared keys,
  - total compared rows.
- The check passes when `(mismatches / total_compared) <= tolerance`.

### Result metadata

Return a `QualityCheckResult` with:

- `metric_name`: `checksum_reconciliation_check`
- `metric_value`: counts for `missing_in_target`, `missing_in_source`, `hash_mismatches`,
  `total_compared`, and `mismatch_pct`.
- `metadata`: query strings, key/column list, normalization settings, and up to 10 sample mismatches.

### Key-level anti-join parity

Introduce `KeyParityCheck` to validate that keys align between source and target tables.

- **Required inputs**:
  - `source_table`
  - `key_columns`
- **Optional inputs**:
  - `partition_column`
  - `where_clause` (source filter)
  - `tolerance` (fraction of missing keys allowed, default 0.0)
- **Behavior**:
  - Compare distinct keys between source and target.
  - Report missing keys on each side.

### Absolute-difference tolerances

Extend reconciliation checks that compare row counts or aggregates to accept an absolute
tolerance in addition to percentage tolerance.

- New optional field: `absolute_tolerance` (default `None`).
- A check passes when **either** percent tolerance or absolute tolerance is satisfied.
- Applies to `ReconciliationCheck` and `AggregateConsistencyCheck`, and is reused by
  `ChecksumReconciliationCheck` where applicable (e.g., mismatches count).

### Multi-aggregate parity

Introduce `MultiAggregateConsistencyCheck` to compare multiple aggregates in a single query.

- **Required inputs**:
  - `source_table`
  - `aggregates`: list of `{name, expression, target_column}` mappings.
- **Optional inputs**:
  - `partition_column`
  - `group_by`
  - `tolerance` / `absolute_tolerance`
- **Behavior**:
  - Compute all aggregates in one source query.
  - Compare each aggregate column in the target with the corresponding source value(s).

## Implementation

- Add `ChecksumReconciliationCheck` to
  `packages/phlo-pandera/src/phlo_quality/reconciliation.py` and export from
  `packages/phlo-pandera/src/phlo_quality/__init__.py`.
- Add `KeyParityCheck` and `MultiAggregateConsistencyCheck` to the same module and exports.
- Extend `ReconciliationCheck` and `AggregateConsistencyCheck` to support `absolute_tolerance`.
- Build source/target queries using Trino SQL with deterministic normalization.
- Extend tests in `packages/phlo-pandera/tests/test_quality_reconciliation.py` to cover
  matching hashes, mismatches, missing keys, tolerance, absolute tolerance, and partition filtering.
- Add short documentation to the developer guide alongside other reconciliation checks.

## Consequences

### Positive

- Detects silent row-level drift that count/aggregate checks miss.
- Standardizes checksum reconciliation without user-specific SQL.
- Adds key parity and multi-aggregate primitives for common lakehouse checks.
- Supports absolute tolerances for small tables and low-volume partitions.
- Leverages existing Trino integration and partition-aware patterns.

### Negative

- More expensive than count/aggregate checks on large tables.
- Requires careful normalization to avoid false positives.
- May be sensitive to nondeterministic columns unless explicitly excluded.
- Additional API surface increases maintenance burden.

## Verification

- Run checksum reconciliation on a known-good dataset and verify zero mismatches.
- Introduce controlled row-level modifications and confirm mismatches are detected.
- Validate tolerance (percent + absolute), sampling, and partition filters with unit tests and
  integration tests.
- Validate key parity and multi-aggregate checks against known mismatches.
