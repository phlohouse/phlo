"""Quality check naming and metadata contract.

This module defines a contract for asset check metadata so downstream consumers
(e.g., Observatory UI) can render results consistently without special-casing
check implementations. The contract standardizes naming conventions, severity
policies, partition semantics, and metadata keys across different check types
(Pandera, dbt, and Phlo native checks).

Contract Overview:

Naming Convention:
    - Pandera schema contract check name: ``pandera_contract``
    - dbt test check name: ``dbt__<test_type>__<target>``
    - Quality check names are derived from the check class or user-provided name

Severity Policy:
    - Pandera schema contract checks are blocking and emit ERROR on failure
    - dbt tests default to ERROR for ``not_null``, ``unique``, ``relationships``
    - Other dbt test types default to WARN
    - dbt tag overrides:
        - ``tag:blocking`` forces ERROR severity
        - ``tag:warn`` or ``tag:anomaly`` forces WARN severity

Partition Semantics:
    - If a Dagster run provides a partition key, checks are scoped to that
      partition by default
    - Default partition column: ``_phlo_partition_date`` (YYYY-MM-DD format)
    - Override per-check via ``partition_column`` parameter
    - Unpartitioned checks may use rolling window via ``rolling_window_days``
    - Set ``full_table=True`` to explicitly run without partition scoping

Required Metadata Keys:
    - ``source``: Check source type (``pandera``, ``dbt``, ``phlo``)
    - ``partition_key``: Partition key string when applicable
    - ``failed_count``: Number of failures (schema errors, failed tests, etc.)
    - ``total_count``: Total evaluated (rows, tests run, etc.) when available
    - ``query_or_sql``: SQL/query/command string used for evaluation
    - ``sample``: List of up to 20 sample rows/ids/errors when available

Optional Metadata Keys:
    - ``repro_sql``: Safe SQL snippet for reproducing failures in Trino
      (e.g., with LIMIT clause added)

Example:
    ```python
    from phlo_pandera.contract import QualityCheckContract

    contract = QualityCheckContract(
        source="pandera",
        failed_count=5,
        total_count=1000,
        partition_key="2024-01-15",
        query_or_sql="SELECT * FROM bronze.events WHERE _phlo_partition_date = '2024-01-15'",
        sample=[{"row_index": 42, "error": "type mismatch"}],
    )

    metadata = contract.to_metadata()
    # Returns dict with standardized keys
    ```

See Also:
    - ``severity.py``: Severity mapping functions
    - ``decorator.py``: ``@phlo_pandera`` decorator that produces these contracts
    - ``pandera_asset_checks.py``: Pandera contract evaluation

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PANDERA_CONTRACT_CHECK_NAME = "pandera_contract"


def dbt_check_name(test_type: str, target: str) -> str:
    """Build a canonical ``dbt__<test_type>__<target>`` check name, sanitizing
    the components for Dagster naming constraints.

    Example:
        ```python
        dbt_check_name("not_null", "orders.id")
        # Returns: "dbt__not_null__orders_id"

        dbt_check_name("accepted_values", "orders.status")
        # Returns: "dbt__accepted_values__orders_status"
        ```

    """
    return f"dbt__{_sanitize_dagster_name(test_type)}__{_sanitize_dagster_name(target)}"


def _sanitize_dagster_name(value: str) -> str:
    """Normalize a raw string into a Dagster-safe identifier segment:
    non-alphanumerics become underscores and runs collapse to one; an empty
    result becomes "unknown".

    Example:
        ```python
        _sanitize_dagster_name("orders.id")
        # Returns: "orders_id"

        _sanitize_dagster_name("schema.table.column")
        # Returns: "schema_table_column"

        _sanitize_dagster_name("!!!")
        # Returns: "unknown"
        ```

    """
    cleaned = "".join(char if char.isalnum() else "_" for char in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "unknown"


@dataclass(frozen=True, slots=True)
class QualityCheckContract:
    """Canonical metadata payload for quality checks, consumable by
    downstream systems such as the Observatory UI or alerting. Frozen and
    slotted for immutability and cheap creation.

    Example:
        ```python
        contract = QualityCheckContract(
            source="phlo",
            failed_count=3,
            total_count=500,
            partition_key="2024-01-15",
            query_or_sql="SELECT * FROM bronze.events",
            repro_sql="SELECT * FROM bronze.events LIMIT 100",
            sample=[
                {"row_index": 10, "error": "null value in required column"},
            ],
        )

        metadata = contract.to_metadata()
        ```

    """

    source: Literal["pandera", "dbt", "phlo"]
    failed_count: int
    partition_key: str | None = None
    total_count: int | None = None
    query_or_sql: str | None = None
    repro_sql: str | None = None
    sample: list[Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Export all non-None contract fields as a metadata dictionary with
        standardized keys; samples are trimmed to 20 items on export.

        Example:
            ```python
            contract = QualityCheckContract(
                source="pandera",
                failed_count=5,
                partition_key="2024-01-15",
            )
            metadata = contract.to_metadata()
            # Returns: {"source": "pandera", "failed_count": 5, "partition_key": "2024-01-15"}
            ```

        """
        metadata: dict[str, Any] = {
            "source": self.source,
            "failed_count": self.failed_count,
        }

        if self.partition_key is not None:
            metadata["partition_key"] = self.partition_key

        if self.total_count is not None:
            metadata["total_count"] = self.total_count

        if self.query_or_sql is not None:
            metadata["query_or_sql"] = self.query_or_sql

        if self.repro_sql is not None:
            metadata["repro_sql"] = self.repro_sql

        if self.sample is not None:
            metadata["sample"] = self.sample[:20]

        return metadata

    def to_dagster_metadata(self) -> dict[str, Any]:
        """Backwards-compatible alias delegating to to_metadata()."""
        return self.to_metadata()
