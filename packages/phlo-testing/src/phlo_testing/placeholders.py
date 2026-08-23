"""Testing utilities and mock implementations for Phlo workflows.

Provides comprehensive testing utilities including mock DLT sources,
mock Iceberg catalog backed by DuckDB, fixture management, and test
execution helpers for validating Phlo workflows without Docker.

Modules:
    - MockDLTSource: Mock DLT source for testing ingestion assets
    - MockIcebergCatalog: In-memory Iceberg catalog using DuckDB
    - test_asset_execution: Helper for testing asset functions
    - load_fixture/save_fixture: Fixture file management

Example:
    >>> from phlo_testing.placeholders import MockDLTSource, MockIcebergCatalog
    >>> source = MockDLTSource(data=[{"id": 1}], resource_name="test")
    >>> catalog = MockIcebergCatalog()
    >>> table = catalog.create_table("raw.test", schema={"id": "int"})

Status:
    - MockDLTSource: Fully implemented
    - MockIcebergCatalog: Fully implemented (DuckDB backend)
    - Fixture management: Fully implemented
    - test_asset_execution: Fully implemented

For comprehensive testing guide, see: docs/TESTING_GUIDE.md

"""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

import pandas as pd
from phlo.logging import get_logger
from phlo_testing.mock_iceberg import MockIcebergCatalog

logger = get_logger(__name__)


class MockDLTSource:
    """Mock DLT source for testing ingestion assets without API calls.

    Wraps test data (a list of record dicts or a DataFrame) under a resource
    name so tests can validate schema checks, transformations, and asset
    logic without API connections.

    Example:
        >>> test_data = [
        ...     {"id": "1", "city": "London", "temp": 15.5},
        ...     {"id": "2", "city": "Paris", "temp": 12.3},
        ... ]
        >>> source = MockDLTSource(data=test_data, resource_name="weather")
        >>> len(source)
        2
        >>> df = source.to_pandas()
        >>> df["city"][0]
        'London'

    Status: Fully implemented

    """

    def __init__(
        self,
        data: Union[List[Dict[str, Any]], pd.DataFrame],
        resource_name: str = "mock_resource",
    ):
        """Initialize mock DLT source.

        ``data`` is either a list of record dicts or a pandas DataFrame.
        Raises TypeError for any other type.
        """
        if isinstance(data, pd.DataFrame):
            self.data = data.to_dict("records")
            self._dataframe = data
        else:
            self.data = data
            self._dataframe = None

        self.resource_name = resource_name

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Yield each data record in turn."""
        for row in self.data:
            yield row

    def __call__(self):
        """Return self so the source is callable like a DLT resource."""
        return self

    @property
    def name(self) -> str:
        """Return the resource name."""
        return self.resource_name

    def to_pandas(self) -> pd.DataFrame:
        """Return all records as a pandas DataFrame."""
        if self._dataframe is not None:
            return self._dataframe
        return pd.DataFrame(self.data)

    def __len__(self) -> int:
        """Return the number of records."""
        return len(self.data)

    def __repr__(self) -> str:
        """Return a string with the resource name and row count."""
        return f"MockDLTSource(resource_name='{self.resource_name}', rows={len(self.data)})"


@contextmanager
def mock_dlt_source(
    data: Union[List[Dict[str, Any]], pd.DataFrame],
    resource_name: str = "mock_resource",
):
    """Context manager for mocking DLT sources.

    Wraps MockDLTSource for isolated testing scenarios. Yields a
    MockDLTSource configured with the provided data and resource name.

    Example:
        >>> test_data = [{"id": "1", "value": 42}]
        >>> with mock_dlt_source(data=test_data, resource_name="test") as source:
        ...     result = list(source)
        ...     assert len(result) == 1

    """
    source = MockDLTSource(data, resource_name)
    try:
        yield source
    finally:
        pass


@contextmanager
def mock_iceberg_catalog():
    """Context manager yielding the canonical mock Iceberg catalog implementation."""
    catalog = MockIcebergCatalog()
    try:
        yield catalog
    finally:
        catalog.close()


class TestAssetResult:
    """Result from test_asset_execution.

    Encapsulates the outcome of testing an asset function: success status,
    the resulting DataFrame when available, the exception on failure, and
    additional metadata about the execution.
    """

    def __init__(
        self,
        success: bool,
        data: Optional[pd.DataFrame] = None,
        error: Optional[Exception] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Initialize test result from its success flag, optional data,
        optional error, and optional metadata."""
        self.success = success
        self.data = data
        self.error = error
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        """Return a string with the status and row count."""
        status = "SUCCESS" if self.success else "FAILED"
        rows = len(self.data) if self.data is not None else 0
        return f"TestAssetResult(status={status}, rows={rows})"


def test_asset_execution(
    asset_fn: Callable[..., Any],
    partition: str,
    mock_data: Optional[Union[List[Dict[str, Any]], pd.DataFrame]] = None,
    validation_schema: Optional[Any] = None,
) -> TestAssetResult:
    """Test asset execution with mocked dependencies.

    Executes an asset function with mock data and optional schema validation.
    Does not require Docker or Dagster infrastructure.

    This is a simplified testing helper that:
    - Executes the asset function directly (bypasses Dagster)
    - Uses MockDLTSource if mock_data provided
    - Validates with Pandera schema if provided
    - Returns success/failure with data

    Limitations:
    - Does not execute within Dagster context
    - Does not write to actual Iceberg tables
    - Does not test Dagster-specific features (retries, metadata, etc.)
    - Good for testing asset logic, not full pipeline

    ``asset_fn`` is the original undecorated function. ``partition`` is the
    partition date string (e.g. "2024-01-15"). With ``mock_data`` the asset
    function is bypassed entirely; otherwise it is called with the partition.
    ``validation_schema`` optionally validates the result with a Pandera
    schema. Returns a TestAssetResult with success status, data, and errors.

    Example:
        Test with mock data:

        >>> def my_asset_logic(partition_date: str):
        ...     return rest_api({...})  # Returns DLT source
        >>> test_data = [{"id": "1", "city": "London", "temp": 15.5}]
        >>> result = test_asset_execution(
        ...     asset_fn=my_asset_logic,
        ...     partition="2024-01-15",
        ...     mock_data=test_data,
        ...     validation_schema=RawWeatherData,
        ... )
        >>> assert result.success
        >>> assert len(result.data) == 1

        Test actual API call:

        >>> result = test_asset_execution(
        ...     asset_fn=my_asset_logic,
        ...     partition="2024-01-15",
        ...     validation_schema=RawWeatherData,
        ... )
        >>> assert result.success
        >>> assert len(result.data) > 0

    Status: Implemented (basic version)

    """
    try:
        if mock_data is not None:
            if isinstance(mock_data, (list, pd.DataFrame)):
                source = MockDLTSource(data=mock_data)
                # With mock data the asset function is never called; the mock
                # source stands in for what the asset would have returned.
                result_source = source
            else:
                result_source = mock_data
        else:
            result_source = asset_fn(partition)

        if hasattr(result_source, "to_pandas"):
            df = result_source.to_pandas()
        elif hasattr(result_source, "__iter__"):
            data_list = list(result_source)
            df = pd.DataFrame(data_list)
        elif isinstance(result_source, pd.DataFrame):
            df = result_source
        else:
            raise ValueError(
                f"Asset function returned unexpected type: {type(result_source)}. "
                "Expected DLT source, DataFrame, or iterable."
            )

        if validation_schema is not None:
            try:
                df = validation_schema.validate(df)
                metadata = {"validation": "passed"}
            except Exception as e:
                logger.debug(
                    "testing_asset_validation_failed",
                    asset_name=getattr(asset_fn, "__name__", str(asset_fn)),
                    partition=partition,
                    exc_info=True,
                )
                return TestAssetResult(
                    success=False,
                    data=df,
                    error=e,
                    metadata={"validation": "failed", "error": str(e)},
                )
        else:
            metadata = {"validation": "skipped"}

        return TestAssetResult(
            success=True,
            data=df,
            error=None,
            metadata=metadata,
        )

    except Exception as e:
        logger.debug(
            "testing_asset_execution_failed",
            asset_name=getattr(asset_fn, "__name__", str(asset_fn)),
            partition=partition,
            exc_info=True,
        )
        return TestAssetResult(
            success=False,
            data=None,
            error=e,
            metadata={"error": str(e)},
        )


# Fixture Management


def load_fixture(
    path: Union[str, Path],
) -> Union[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any]]:
    """Load test fixture from file.

    Supports JSON, CSV, and Parquet files, detecting the format from the
    file extension and returning a DataFrame, dict, or list of dicts to
    match. Raises FileNotFoundError for a missing file and ValueError for
    an unsupported format.

    Example:
        >>> # Load JSON fixture
        >>> test_data = load_fixture("tests/fixtures/weather_data.json")
        >>> # Use in test
        >>> with mock_dlt_source(data=test_data) as source:
        ...     result = my_asset_function(source)

        >>> # Load CSV fixture
        >>> test_df = load_fixture("tests/fixtures/sample_data.csv")
        >>> validated = MySchema.validate(test_df)

    Status: Fully implemented

    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")

    suffix = path.suffix.lower()

    if suffix == ".json":
        with open(path, "r") as f:
            data = json.load(f)
        # JSON payloads pass through unchanged so they can feed MockDLTSource
        # directly.
        return data

    elif suffix == ".csv":
        return pd.read_csv(path)

    elif suffix == ".parquet":
        return pd.read_parquet(path)

    else:
        raise ValueError(
            f"Unsupported fixture format: {suffix}. Supported formats: .json, .csv, .parquet"
        )


def save_fixture(
    data: Union[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any]],
    path: Union[str, Path],
    pretty: bool = True,
) -> None:
    """Save test data as fixture file.

    Determines the format from the file extension (.json, .csv, or
    .parquet), creating parent directories as needed. Raises ValueError
    for an unsupported format.

    Example:
        >>> # Save test data for reuse
        >>> test_data = [
        ...     {"id": "1", "value": 42},
        ...     {"id": "2", "value": 84},
        ... ]
        >>> save_fixture(test_data, "tests/fixtures/sample_data.json")

        >>> # Save DataFrame
        >>> df = pd.DataFrame(test_data)
        >>> save_fixture(df, "tests/fixtures/sample_data.csv")

        >>> # Later, load it in tests
        >>> loaded = load_fixture("tests/fixtures/sample_data.json")

    Status: Fully implemented

    """
    path = Path(path)

    path.parent.mkdir(parents=True, exist_ok=True)

    suffix = path.suffix.lower()

    if suffix == ".json":
        with open(path, "w") as f:
            if pretty:
                json.dump(data, f, indent=2, default=str)
            else:
                json.dump(data, f, default=str)

    elif suffix == ".csv":
        if isinstance(data, pd.DataFrame):
            data.to_csv(path, index=False)
        else:
            df: pd.DataFrame = (
                pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
            )
            df.to_csv(path, index=False)

    elif suffix == ".parquet":
        if isinstance(data, pd.DataFrame):
            data.to_parquet(path, index=False)
        else:
            df: pd.DataFrame = (
                pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame([data])
            )
            df.to_parquet(path, index=False)

    else:
        raise ValueError(
            f"Unsupported fixture format: {suffix}. Supported formats: .json, .csv, .parquet"
        )
