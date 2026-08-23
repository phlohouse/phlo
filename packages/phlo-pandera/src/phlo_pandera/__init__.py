"""Phlo Quality Framework - Declarative data quality checks.

This module provides the Phlo Quality Framework, a declarative approach to data quality
checks that reduces boilerplate by approximately 70% compared to manual implementations.

The framework integrates with Dagster to automatically generate asset checks from
declarative quality check definitions, providing seamless quality validation
within your data pipelines.

Basic Usage:
    ```python
    from phlo_pandera import NullCheck, RangeCheck, phlo_pandera

    @phlo_pandera(
        table="bronze.weather_observations",
        checks=[
            NullCheck(columns=["station_id", "temperature"]),
            RangeCheck(column="temperature", min_value=-50, max_value=60),
        ],
    )
    def weather_quality():
        pass
    ```

Quality Check Architecture:
    The quality check system consists of multiple layers:

    1. **Check Classes**: Define validation rules (NullCheck, RangeCheck, etc.)
    2. **Decorator**: ``@phlo_pandera`` wraps checks into Dagster asset checks
    3. **Runtime**: Executes checks against data and emits results
    4. **Metadata**: Structured output for observability and debugging

Available Quality Checks:
    - **NullCheck**: Verify no null values in specified columns
    - **RangeCheck**: Verify numeric values within a defined range
    - **FreshnessCheck**: Verify data recency based on timestamp columns
    - **UniqueCheck**: Verify uniqueness constraints across columns
    - **CountCheck**: Verify row count meets minimum/maximum bounds
    - **SchemaCheck**: Verify Pandera schema compliance
    - **CustomSQLCheck**: Execute arbitrary SQL assertions
    - **PatternCheck**: Verify string values match regex patterns

Reconciliation Checks:
    - **ReconciliationCheck**: Compare row counts between source and target tables
    - **AggregateConsistencyCheck**: Verify computed aggregates match expectations
    - **KeyParityCheck**: Ensure matching keys between source and target
    - **MultiAggregateConsistencyCheck**: Compare multiple aggregates efficiently
    - **ChecksumReconciliationCheck**: Validate row-level data integrity

Examples:
    Basic null and range checks:
        ```python
        from phlo_pandera import NullCheck, RangeCheck, phlo_pandera

        @phlo_pandera(
            table="bronze.sensor_readings",
            checks=[
                NullCheck(columns=["sensor_id", "reading_value"]),
                RangeCheck(column="reading_value", min_value=0, max_value=100),
            ],
        )
        def sensor_quality():
            pass
        ```

    Comprehensive validation with multiple check types:
        ```python
        from phlo_pandera import (
            NullCheck,
            RangeCheck,
            FreshnessCheck,
            UniqueCheck,
            CountCheck,
            phlo_pandera,
        )

        @phlo_pandera(
            table="bronze.events",
            checks=[
                NullCheck(columns=["event_id", "timestamp"]),
                RangeCheck(column="value", min_value=0, max_value=1000),
                FreshnessCheck(timestamp_column="timestamp", max_age_hours=2),
                UniqueCheck(columns=["event_id"]),
                CountCheck(min_rows=100),
            ],
            group="events",
        )
        def event_quality():
            pass
        ```

    Permissive thresholds for data with expected imperfections:
        ```python
        @phlo_pandera(
            table="bronze.customer_data",
            checks=[
                NullCheck(columns=["phone"], allow_threshold=0.05),  # Allow 5% nulls
                RangeCheck(column="age", min_value=0, max_value=150, allow_threshold=0.01),
            ],
            warn_threshold=0.3,  # Warn if >30% of checks fail
        )
        def customer_quality():
            pass
        ```

``__version__`` holds the package version string.
"""

from phlo_pandera.checks import (
    CountCheck,
    FreshnessCheck,
    NullCheck,
    QualityCheck,
    RangeCheck,
    UniqueCheck,
)
from phlo_pandera.checks_extra import CustomSQLCheck, PatternCheck, SchemaCheck
from phlo_pandera.contract import PANDERA_CONTRACT_CHECK_NAME, QualityCheckContract, dbt_check_name
from phlo_pandera.decorator import clear_quality_checks, get_quality_checks, phlo_pandera
from phlo_pandera.helpers import (
    accepted_values_check,
    checks_from_contract,
    freshness_check_from_sla,
    required_field_null_checks,
    unique_key_check,
)
from phlo_pandera.schema_extractor import PanderaSchemaExtractor
from phlo_pandera.reconciliation import (
    AggregateConsistencyCheck,
    AggregateSpec,
    ChecksumReconciliationCheck,
    KeyParityCheck,
    MultiAggregateConsistencyCheck,
    ReconciliationCheck,
)

__all__ = [
    # Decorator (use as @phlo_pandera(...))
    "phlo_pandera",
    "get_quality_checks",
    "clear_quality_checks",
    # Base class
    "QualityCheck",
    # Quality checks
    "NullCheck",
    "RangeCheck",
    "FreshnessCheck",
    "UniqueCheck",
    "CountCheck",
    "SchemaCheck",
    "CustomSQLCheck",
    "PatternCheck",
    # Helper factories
    "accepted_values_check",
    "checks_from_contract",
    "freshness_check_from_sla",
    "required_field_null_checks",
    "unique_key_check",
    # Reconciliation checks
    "ReconciliationCheck",
    "AggregateConsistencyCheck",
    "AggregateSpec",
    "KeyParityCheck",
    "MultiAggregateConsistencyCheck",
    "ChecksumReconciliationCheck",
    # Schema extraction
    "PanderaSchemaExtractor",
    # Contract helpers
    "PANDERA_CONTRACT_CHECK_NAME",
    "QualityCheckContract",
    "dbt_check_name",
]

__version__ = "0.14.0"
