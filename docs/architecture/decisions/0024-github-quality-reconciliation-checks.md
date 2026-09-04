# 24. GitHub Example: phlo_quality Reconciliation Checks

Date: 2025-12-19

## Status

Accepted

## Beads

- `phlo-2md`: Docs/examples: Add phlo_quality reconciliation checks (GitHub)

## Context

Users need working examples demonstrating how to use `phlo_quality` for warehouse-native reconciliation and aggregate consistency checks. These checks ensure data integrity across pipeline stages:

- **Row count parity**: Verify staging tables match expected row counts in fact tables (per partition)
- **Aggregate consistency**: Verify computed aggregates match source data sums
- **Freshness validation**: Ensure data is current for each partition

The GitHub example project already has basic quality checks (null checks, range checks, uniqueness) but lacks reconciliation checks that validate data consistency **across tables**.

## Decision

Add two partition-aware reconciliation checks to the GitHub example:

### 1. ReconciliationCheck - Row Count Parity

A new `ReconciliationCheck` class that compares row counts between staging and fact tables for a given partition:

```python
import phlo
@phlo.quality.pandera(
    table="gold.fct_daily_github_metrics",
    checks=[
        ReconciliationCheck(
            source_table="silver.stg_github_events",
            partition_column="_phlo_partition_date",
            check_type="rowcount_parity",
            tolerance=0.0,  # Exact match
        ),
    ],
    partition_aware=True,
)
def daily_metrics_reconciliation():
    """Verify fct_daily_github_metrics row count matches source events."""
    pass
```

### 2. AggregateConsistencyCheck - Sum Verification

A check that validates computed aggregates match their source:

```python
import phlo
@phlo.quality.pandera(
    table="gold.fct_daily_github_metrics",
    checks=[
        AggregateConsistencyCheck(
            source_table="silver.stg_github_events",
            aggregate_column="total_events",
            source_expression="COUNT(*)",
            partition_column="_phlo_partition_date",
            group_by=["activity_date"],
            tolerance=0.0,
        ),
    ],
    partition_aware=True,
)
def daily_metrics_aggregate_consistency():
    """Verify total_events count matches source row count."""
    pass
```

## Implementation

### New Files

#### [NEW] `packages/phlo-pandera/src/phlo_quality/reconciliation.py`

Contains:

- `ReconciliationCheck`: Compare row counts or other metrics across tables
- `AggregateConsistencyCheck`: Verify aggregates match source computations

#### [MODIFY] `phlo-examples/github/workflows/quality/github.py`

Add reconciliation check examples using the new check classes.

#### [MODIFY] `phlo-examples/github/workflows/quality/__init__.py`

Optional: keep module exports for organization; no registration step is required.

### Verification Plan

#### Automated Tests

```bash
# Run quality module tests
pytest tests/test_quality*.py -v

# Run strict validation tests (related)
pytest packages/phlo-dlt/tests/test_strict_validation.py -v
```

#### Manual Verification

1. Materialize the GitHub example pipeline
2. Check Dagster UI for asset checks appearing on `fct_daily_github_metrics`
3. Verify the check metadata includes partition key and row count comparison

## Consequences

- GitHub example becomes a comprehensive reference for reconciliation patterns
- Users have copy-paste examples for common warehouse validation scenarios
- New check classes can be reused across other projects

## Documentation

Add a brief debugging workflow section to the GitHub example README showing:

1. How to identify a failing reconciliation check in Dagster
2. How to use the SQL query in the check metadata to debug
3. Common causes (late-arriving data, transform logic bugs)
