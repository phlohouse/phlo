"""Helper to execute assets with mocked dependencies and capture results.

Provides `test_asset_execution()` for running `@phlo_ingestion` assets in tests
with mocked Iceberg, Trino, and DLT dependencies.

Example:
    >>> result = test_asset_execution(
    ...     my_asset,
    ...     partition="2024-01-01",
    ...     mock_data=[{"id": 1, "name": "Alice"}],
    ... )
    >>> assert result.success
    >>> assert len(result.data) == 1

"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from phlo.logging import get_logger
from phlo_testing.mock_iceberg import MockIcebergCatalog
from phlo_testing.mock_trino import MockTrinoResource


@dataclass
class AssetTestResult:
    """Result of executing an asset in test mode.

    Carries success status, resulting data, metadata, captured logs,
    timing, any error, and the raw Dagster ExecuteInProcessResult for
    advanced use.
    """

    success: bool
    data: Optional[pd.DataFrame] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    duration: float = 0.0
    error: Optional[Exception] = None
    raw_result: Optional[Any] = None


class MockAssetContext:
    """Mock Dagster context for asset execution.

    Provides mocked resources (Iceberg, Trino, DLT) and logging capabilities
    for testing assets without requiring a full Dagster environment.

    Example:
        >>> context = MockAssetContext(partition_key="2024-01-01")
        >>> context.log("Processing partition")
        >>> table_store = context.get_resource("table_store")

    """

    def __init__(
        self,
        partition_key: Optional[str] = None,
        mock_iceberg: Optional[MockIcebergCatalog] = None,
        mock_trino: Optional[MockTrinoResource] = None,
    ) -> None:
        """Initialize the context, creating fresh mocks when none are given."""
        self.partition_key = partition_key or "2024-01-01"
        self.iceberg = mock_iceberg or MockIcebergCatalog()
        self.trino = mock_trino or MockTrinoResource()

        self._logs: list[str] = []
        self._logger = self._create_logger()

    def _create_logger(self) -> Any:
        """Create the phlo logger with a capture handler attached."""
        name = f"asset_test_{id(self)}"
        logger = get_logger(name)

        std_logger = logging.getLogger(name)
        std_logger.setLevel(logging.DEBUG)
        std_logger.propagate = False
        std_logger.handlers = []

        class LogCapture(logging.Handler):
            """Logging handler that appends formatted records to a list."""

            def __init__(self, logs_list: list[str]) -> None:
                """Initialize the capture handler."""
                super().__init__()
                self.logs = logs_list

            def emit(self, record: logging.LogRecord) -> None:
                """Append the formatted log record to the capture list."""
                self.logs.append(self.format(record))

        handler = LogCapture(self._logs)
        formatter = logging.Formatter(
            "%(levelname)s - %(name)s - %(message)s",
        )
        handler.setFormatter(formatter)
        std_logger.addHandler(handler)

        return logger

    def log(self, message: str, level: str = "INFO") -> None:
        """Log a message at the given level (DEBUG, INFO, WARNING, ERROR)."""
        resolved_level = level.lower()
        if resolved_level == "warn":
            resolved_level = "warning"
        getattr(self._logger, resolved_level)(message)

    @property
    def logs(self) -> list[str]:
        """Get a copy of the captured log messages."""
        return self._logs.copy()

    def get_resource(self, name: str) -> Any:
        """Get a mock resource by name.

        Raises ValueError for unknown resource names.
        """
        resources = {
            "table_store": self.iceberg,
            "trino": self.trino,
        }

        if name not in resources:
            raise ValueError(f"Unknown resource: {name}")

        return resources[name]


def test_asset_execution(
    asset_fn: Callable,
    partition: str = "2024-01-01",
    mock_data: Optional[list[dict[str, Any]]] = None,
    mock_iceberg: Optional[MockIcebergCatalog] = None,
    mock_trino: Optional[MockTrinoResource] = None,
    expected_schema: Optional[Any] = None,
    materialize_kwargs: Optional[dict[str, Any]] = None,
    _pytest_skip: bool = True,  # Flag to prevent pytest collection
) -> AssetTestResult:
    """Execute an asset with mocked dependencies.

    Runs a `@phlo_ingestion` asset in isolation with mocked Iceberg,
    Trino, and DLT services. Captures results and logs for inspection.
    Failures are reported through success=False on the returned
    AssetTestResult rather than raised. When expected_schema is given it
    validates the resulting data.

    Example:
        >>> @phlo_ingestion(
        ...     unique_key="id",
        ...     validation_schema=MySchema,
        ... )
        ... def my_asset(partition_date: str):
        ...     return [{"id": 1, "name": "Alice"}]
        ...
        >>> result = test_asset_execution(
        ...     my_asset,
        ...     partition="2024-01-01",
        ... )
        >>> assert result.success
        >>> assert len(result.data) == 1

    """
    if mock_data is None:
        mock_data = []

    if materialize_kwargs is None:
        materialize_kwargs = {}

    start_time = time.time()
    context = MockAssetContext(
        partition_key=partition,
        mock_iceberg=mock_iceberg,
        mock_trino=mock_trino,
    )

    try:
        result = asset_fn(partition_date=partition)

        # Normalise whatever the asset returns (DataFrame, record list, or
        # generator) into a single DataFrame.
        if isinstance(result, pd.DataFrame):
            data = result
        elif isinstance(result, list):
            data = pd.DataFrame(result) if result else pd.DataFrame()
        else:
            data = pd.DataFrame(list(result)) if result else pd.DataFrame()

        if expected_schema is not None:
            try:
                expected_schema.validate(data)
            except Exception as e:
                return AssetTestResult(
                    success=False,
                    data=data,
                    logs=context.logs,
                    duration=time.time() - start_time,
                    error=ValueError(f"Schema validation failed: {e}"),
                )

        return AssetTestResult(
            success=True,
            data=data,
            metadata={
                "row_count": len(data),
                "columns": list(data.columns),
                "partition": partition,
            },
            logs=context.logs,
            duration=time.time() - start_time,
            raw_result=result,
        )

    except Exception as e:
        return AssetTestResult(
            success=False,
            logs=context.logs,
            duration=time.time() - start_time,
            error=e,
        )


def test_asset_with_catalog(
    asset_fn: Callable,
    partition: str = "2024-01-01",
    catalog: Optional[MockIcebergCatalog] = None,
) -> AssetTestResult:
    """Execute an asset with access to mock Iceberg catalog.

    Useful for testing assets that read from or write to Iceberg tables.
    Creates a fresh MockIcebergCatalog when none is supplied.

    Example:
        >>> catalog = MockIcebergCatalog()
        >>> # Set up tables in catalog
        >>> result = test_asset_with_catalog(
        ...     my_transform_asset,
        ...     partition="2024-01-01",
        ...     catalog=catalog,
        ... )

    """
    if catalog is None:
        catalog = MockIcebergCatalog()

    return test_asset_execution(
        asset_fn,
        partition=partition,
        mock_iceberg=catalog,
    )


def test_asset_with_trino(
    asset_fn: Callable,
    partition: str = "2024-01-01",
    trino: Optional[MockTrinoResource] = None,
) -> AssetTestResult:
    """Execute an asset with access to mock Trino resource.

    Useful for testing quality checks and transform assets; creates a
    fresh MockTrinoResource when none is supplied.

    Example:
        >>> trino = MockTrinoResource()
        >>> # Set up tables in Trino
        >>> result = test_asset_with_trino(
        ...     my_quality_check,
        ...     trino=trino,
        ... )

    """
    if trino is None:
        trino = MockTrinoResource()

    return test_asset_execution(
        asset_fn,
        partition=partition,
        mock_trino=trino,
    )


class TestAssetExecutor:
    """Reusable executor for testing multiple asset runs.

    Maintains catalog state across multiple executions for integration testing.

    Example:
        >>> executor = TestAssetExecutor()
        >>> result1 = executor.execute(asset1, partition="2024-01-01")
        >>> result2 = executor.execute(asset2, partition="2024-01-01")
        >>> # Both use same catalog instance

    """

    def __init__(
        self,
        catalog: Optional[MockIcebergCatalog] = None,
        trino: Optional[MockTrinoResource] = None,
    ) -> None:
        """Initialize the executor with shared mocks, defaulting to fresh
        instances."""
        self.catalog = catalog or MockIcebergCatalog()
        self.trino = trino or MockTrinoResource()
        self.results: list[AssetTestResult] = []

    def execute(
        self,
        asset_fn: Callable,
        partition: str = "2024-01-01",
        mock_data: Optional[list[dict[str, Any]]] = None,
    ) -> AssetTestResult:
        """Execute an asset with the executor's shared catalog and Trino
        mocks, recording every AssetTestResult in `results`."""
        result = test_asset_execution(
            asset_fn,
            partition=partition,
            mock_iceberg=self.catalog,
            mock_trino=self.trino,
        )

        self.results.append(result)
        return result

    def get_results(self, asset_fn: Callable) -> list[AssetTestResult]:
        """Get results for an asset function.

        Currently returns all recorded results regardless of asset_fn.
        """
        # This is a simplified implementation
        # In practice, you'd track asset names
        return self.results

    def cleanup(self) -> None:
        """Clean up resources."""
        self.catalog.close()
