"""Partition scoping utilities for SQL quality checks.

Apply partition-based scoping to SQL queries used in quality checks via explicit
partition keys, rolling date windows, or full-table scans (partition filtering
disabled). Scoping integrates with Dagster's partition mechanism, applying the
correct filter based on the execution context.

Example:
    ```python
    from phlo_pandera.partitioning import PartitionScope, apply_partition_scope

    # Scope to specific partition
    scope = PartitionScope(
        partition_key="2024-01-15",
        partition_column="_phlo_partition_date",
        rolling_window_days=None,
        full_table=False,
    )
    sql = apply_partition_scope("SELECT * FROM events", scope=scope)
    # Result: SELECT * FROM events\nWHERE _phlo_partition_date = DATE '2024-01-15'

    # Rolling window (last 7 days)
    scope = PartitionScope(
        partition_key=None,
        partition_column="event_date",
        rolling_window_days=7,
        full_table=False,
    )
    sql = apply_partition_scope("SELECT * FROM events", scope=scope)
    # Result includes: WHERE event_date >= DATE 'YYYY-MM-DD'

    # Full table scan (no partition filtering)
    scope = PartitionScope(
        partition_key=None,
        partition_column="_phlo_partition_date",
        rolling_window_days=None,
        full_table=True,
    )
    sql = apply_partition_scope("SELECT * FROM events", scope=scope)
    # Result: SELECT * FROM events (unchanged)
    ```

Used by ``decorator.py`` (via ``@phlo_pandera``) and by the quality checks in
``checks.py`` that operate on partitioned data.

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class PartitionScope:
    """Partition filter scope for SQL quality checks.

    Defines how a quality check query is scoped to data partitions: an explicit
    ``partition_key`` (typically YYYY-MM-DD) targets a single partition of
    ``partition_column`` (default "_phlo_partition_date"); otherwise, when
    ``rolling_window_days`` is set and no key is given, the query filters to the
    last N days of data. ``full_table=True`` skips all partition filtering,
    useful for historical analysis or small dimension tables.

    Example:
        ```python
        # Specific partition
        scope = PartitionScope(
            partition_key="2024-01-15",
            partition_column="_phlo_partition_date",
            full_table=False,
        )

        # Rolling 7-day window
        scope = PartitionScope(
            partition_key=None,
            partition_column="event_date",
            rolling_window_days=7,
            full_table=False,
        )

        # Full table
        scope = PartitionScope(
            partition_key=None,
            partition_column="_phlo_partition_date",
            full_table=True,
        )
        ```

    """

    partition_key: str | None
    partition_column: str
    rolling_window_days: int | None
    full_table: bool


def get_partition_key(context: object) -> str | None:
    """Extract a partition key from a Dagster-like execution context.

    Tries ``context.partition_key``, then ``context.asset_partition_key``;
    returns the first non-empty string value (typically YYYY-MM-DD), otherwise
    None.
    Example:
        ```python
        # In a Dagster asset
        from dagster import OpExecutionContext

        @asset(partitions_def=DailyPartitionsDefinition(start_date="2024-01-01"))
        def my_asset(context: OpExecutionContext):
            partition_key = get_partition_key(context)
            # Returns: "2024-01-15" (for example)
        ```

    """

    partition_key = getattr(context, "partition_key", None)
    if isinstance(partition_key, str) and partition_key:
        return partition_key

    asset_partition_key = getattr(context, "asset_partition_key", None)
    if isinstance(asset_partition_key, str) and asset_partition_key:
        return asset_partition_key

    return None


def apply_partition_scope(sql: str, *, scope: PartitionScope) -> str:
    """Apply partition scope predicates to a SQL statement.

    Appends WHERE clauses per the scope: an equality filter for
    ``scope.partition_key``, or a date-range filter when ``rolling_window_days``
    is set. Returns the original SQL unchanged when ``scope.full_table`` is True
    or no scoping rules are configured.

    Example:
        ```python
        # Apply specific partition filter
        scope = PartitionScope(
            partition_key="2024-01-15",
            partition_column="_phlo_partition_date",
            rolling_window_days=None,
            full_table=False,
        )
        result = apply_partition_scope("SELECT * FROM events", scope=scope)
        # Returns: SELECT * FROM events\nWHERE _phlo_partition_date = DATE '2024-01-15'

        # Apply rolling window
        scope = PartitionScope(
            partition_key=None,
            partition_column="event_date",
            rolling_window_days=30,
            full_table=False,
        )
        result = apply_partition_scope("SELECT * FROM events", scope=scope)
        # Returns query with: WHERE event_date >= DATE 'YYYY-MM-DD' (30 days ago)
        ```

    """

    if scope.full_table:
        return sql

    if scope.partition_key is not None:
        return _append_where(
            sql, f"{scope.partition_column} = {_date_literal(scope.partition_key)}"
        )

    if scope.rolling_window_days is None:
        return sql

    cutoff = date.today() - timedelta(days=scope.rolling_window_days)
    return _append_where(sql, f"{scope.partition_column} >= {_date_literal(cutoff.isoformat())}")


def _date_literal(value: str) -> str:
    """Format a SQL date literal.

    Converts an ISO date string (YYYY-MM-DD) to a SQL DATE literal expression.

    Example:
        ```python
        _date_literal("2024-01-15")
        # Returns: "DATE '2024-01-15'"
        ```

    """

    return f"DATE '{value}'"


def _append_where(sql: str, condition: str) -> str:
    """Append a condition to SQL with WHERE or AND.

    Appends ``condition`` with AND when the query already contains a WHERE
    clause, otherwise opens a new WHERE clause.

    Example:
        ```python
        # Query without WHERE
        _append_where("SELECT * FROM events", "id > 100")
        # Returns: SELECT * FROM events\nWHERE id > 100

        # Query with existing WHERE
        _append_where("SELECT * FROM events WHERE status = 'active'", "id > 100")
        # Returns: SELECT * FROM events WHERE status = 'active'\nAND id > 100
        ```

    """

    # Detection is a substring match and the condition is always appended at
    # the end, so base queries must not keep WHERE after ORDER BY/LIMIT or
    # contain "where" inside a string literal.
    if "where" in sql.lower():
        return f"{sql}\nAND {condition}"
    return f"{sql}\nWHERE {condition}"
