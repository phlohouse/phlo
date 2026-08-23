"""
Pytest fixtures for testing Phlo assets and workflows.

Provides reusable fixtures for common test scenarios including mock resources,
test data, and temporary directories.

Example:
    >>> def test_my_asset(mock_iceberg_catalog, sample_partition_date):
    ...     # Use fixtures automatically
    ...     pass

"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

import pandas as pd
import pytest

from phlo_testing.execution import MockAssetContext
from phlo_testing.hooks import MockHookBus
from phlo_testing.conftest_template import get_conftest_template
from phlo_testing.mock_dlt import MockDLTResource, mock_dlt_source
from phlo_testing.mock_iceberg import MockIcebergCatalog
from phlo_testing.mock_trino import MockTrinoResource

# --- Mock Resource Fixtures ---


@pytest.fixture
def mock_iceberg_catalog() -> Iterator[MockIcebergCatalog]:
    """
    Provide a fresh MockIcebergCatalog for each test.

    Fixture is function-scoped and auto-cleaned up after test.

    Example:
        >>> def test_with_catalog(mock_iceberg_catalog):
        ...     table = mock_iceberg_catalog.create_table(
        ...         "raw.users",
        ...         schema=get_schema(),
        ...     )

    """
    catalog = MockIcebergCatalog()
    yield catalog
    catalog.close()


@pytest.fixture
def mock_trino() -> Iterator[MockTrinoResource]:
    """
    Provide a fresh MockTrinoResource for each test.

    Fixture is function-scoped and auto-cleaned up after test.

    Example:
        >>> def test_with_trino(mock_trino):
        ...     cursor = mock_trino.cursor()
        ...     cursor.execute("SELECT 1 as id")

    """
    trino = MockTrinoResource()
    yield trino
    trino.close()


@pytest.fixture
def mock_asset_context() -> Iterator[MockAssetContext]:
    """
    Provide a fresh MockAssetContext for each test.

    Includes mock Iceberg and Trino resources plus logging capture.

    Example:
        >>> def test_with_context(mock_asset_context):
        ...     context.log("test message")
        ...     assert "test message" in context.logs

    """
    context = MockAssetContext()
    yield context


@pytest.fixture
def mock_hook_bus() -> MockHookBus:
    """Provide a mock hook bus without plugin discovery."""
    return MockHookBus()


@pytest.fixture
def mock_resources(
    mock_iceberg_catalog: MockIcebergCatalog,
    mock_trino: MockTrinoResource,
) -> dict[str, Any]:
    """
    Provide all mock resources in a dict.

    Useful for passing to functions that need multiple resources.

    Example:
        >>> def test_with_resources(mock_resources):
        ...     table_store = mock_resources["table_store"]
        ...     trino = mock_resources["trino"]

    """
    return {
        "table_store": mock_iceberg_catalog,
        "trino": mock_trino,
    }


# --- Test Data Fixtures ---


@pytest.fixture
def sample_partition_date() -> str:
    """
    Provide a standard partition date for tests.

    Returns ISO format date string (e.g., "2024-01-15").

    Example:
        >>> def test_asset(sample_partition_date):
        ...     assert sample_partition_date == "2024-01-15"

    """
    return "2024-01-15"


@pytest.fixture
def sample_partition_range() -> tuple[str, str]:
    """
    Provide a range of partition dates.

    Returns tuple of (start_date, end_date) in ISO format.

    Example:
        >>> def test_backfill(sample_partition_range):
        ...     start, end = sample_partition_range
        ...     # start = "2024-01-01"
        ...     # end = "2024-01-31"

    """
    start = "2024-01-01"
    end = "2024-01-31"
    return (start, end)


@pytest.fixture
def sample_dlt_data() -> list[dict[str, Any]]:
    """
    Provide sample DLT source data.

    Returns list of test records.

    Example:
        >>> def test_ingestion(sample_dlt_data):
        ...     source = mock_dlt_source(sample_dlt_data)

    """
    return [
        {
            "id": 1,
            "name": "Alice",
            "email": "alice@example.com",
            "created_at": "2024-01-15T10:00:00Z",
        },
        {
            "id": 2,
            "name": "Bob",
            "email": "bob@example.com",
            "created_at": "2024-01-15T11:00:00Z",
        },
        {
            "id": 3,
            "name": "Charlie",
            "email": "charlie@example.com",
            "created_at": "2024-01-15T12:00:00Z",
        },
    ]


@pytest.fixture
def sample_dataframe() -> pd.DataFrame:
    """
    Provide a sample DataFrame for testing.

    Example:
        >>> def test_transform(sample_dataframe):
        ...     assert len(sample_dataframe) == 3

    """
    return pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
            "value": [100.5, 200.75, 150.25],
            "date": pd.date_range("2024-01-01", periods=3),
        }
    )


@pytest.fixture
def mock_dlt_source_fixture(sample_dlt_data: list[dict]) -> MockDLTResource:
    """
    Provide a mock DLT source with sample data.

    Example:
        >>> def test_with_source(mock_dlt_source_fixture):
        ...     for record in mock_dlt_source_fixture:
        ...         # Process record

    """
    return mock_dlt_source(sample_dlt_data, resource_name="test_data")


# --- File System Fixtures ---


@pytest.fixture
def temp_staging_dir() -> Iterator[Path]:
    """
    Provide a temporary directory for test files.

    Auto-cleaned up after test.

    Example:
        >>> def test_with_temp_dir(temp_staging_dir):
        ...     parquet_file = temp_staging_dir / "data.parquet"
        ...     df.to_parquet(parquet_file)

    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_data_dir() -> Path:
    """
    Provide path to test data directory.

    Looks for `tests/fixtures/data/` relative to project root.

    Example:
        >>> def test_with_data_dir(test_data_dir):
        ...     data_file = test_data_dir / "users.json"

    """
    # Find project root by looking for pyproject.toml
    current = Path(__file__).parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "tests" / "fixtures" / "data"
        current = current.parent

    # Fallback to temp dir if not found
    return Path(tempfile.gettempdir()) / "test_data"


# --- Composite Fixtures ---


@pytest.fixture
def setup_test_catalog(
    mock_iceberg_catalog: MockIcebergCatalog,
    sample_dataframe: pd.DataFrame,
) -> MockIcebergCatalog:
    """
    Provide a pre-populated test catalog.

    Creates sample tables in the catalog.

    Example:
        >>> def test_with_setup_catalog(setup_test_catalog):
        ...     table = setup_test_catalog.load_table("raw.users")

    """
    from pyiceberg.schema import Schema
    from pyiceberg.types import DoubleType, IntegerType, NestedField, StringType

    schema = Schema(
        NestedField(field_id=1, name="id", type=IntegerType(), required=True),
        NestedField(field_id=2, name="name", type=StringType(), required=True),
        NestedField(field_id=3, name="value", type=DoubleType(), required=False),
    )

    table = mock_iceberg_catalog.create_table("raw.test_data", schema=schema)
    table.append(sample_dataframe)

    return mock_iceberg_catalog


@pytest.fixture
def setup_test_trino(
    mock_trino: MockTrinoResource,
    sample_dataframe: pd.DataFrame,
) -> MockTrinoResource:
    """
    Provide a pre-populated Trino resource.

    Loads sample tables into Trino.

    Example:
        >>> def test_with_setup_trino(setup_test_trino):
        ...     cursor = setup_test_trino.cursor()
        ...     cursor.execute("SELECT * FROM test.sample_data")

    """
    mock_trino.load_table("test.sample_data", sample_dataframe)
    return mock_trino


# --- Data Loading Fixtures ---


@pytest.fixture
def load_json_fixture(test_data_dir: Path) -> Callable[[str], Any]:
    """
    Provide helper to load JSON fixture files.

    Example:
        >>> def test_with_json(load_json_fixture):
        ...     data = load_json_fixture("users.json")

    """

    def _load_json(filename: str) -> Any:
        filepath = test_data_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Fixture not found: {filepath}")

        with open(filepath) as f:
            return json.load(f)

    return _load_json


@pytest.fixture
def load_csv_fixture(test_data_dir: Path) -> Callable[[str], pd.DataFrame]:
    """
    Provide helper to load CSV fixture files.

    Example:
        >>> def test_with_csv(load_csv_fixture):
        ...     df = load_csv_fixture("users.csv")

    """

    def _load_csv(filename: str) -> pd.DataFrame:
        filepath = test_data_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Fixture not found: {filepath}")

        return pd.read_csv(filepath)

    return _load_csv


# --- Configuration Fixtures ---


@pytest.fixture
def test_config() -> dict[str, Any]:
    """
    Provide test configuration overrides.

    Returns dict with test-specific config values.

    Example:
        >>> def test_with_config(test_config, monkeypatch):
        ...     monkeypatch.setenv("PHLO_ENV", "test")

    """
    return {
        "environment": "test",
        "log_level": "DEBUG",
        "parallel_workers": 1,
    }


# --- Session-scoped Fixtures ---


@pytest.fixture(scope="session")
def session_temp_dir() -> Iterator[Path]:
    """
    Provide a temporary directory for the entire test session.

    Useful for shared test data.

    Example:
        >>> def test_with_session_dir(session_temp_dir):
        ...     # Shared across all tests in session

    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="session")
def session_catalog() -> Iterator[MockIcebergCatalog]:
    """
    Provide a catalog shared across all tests in session.

    Use carefully - tests should clean up their own tables.

    Example:
        >>> def test_with_session_catalog(session_catalog):
        ...     # Shared across all tests

    """
    catalog = MockIcebergCatalog()
    yield catalog
    catalog.close()


# --- Parametrization Helpers ---


def create_partition_dates(start: str, end: str, step_days: int = 1) -> list[str]:
    """
    Create partition dates from start to end (inclusive) stepping step_days
    apart, for parametrized tests.

    Example:
        >>> dates = create_partition_dates("2024-01-01", "2024-01-31", step_days=7)
        >>> # ["2024-01-01", "2024-01-08", "2024-01-15", ...]

    """
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    dates = []
    current = start_dt

    while current <= end_dt:
        dates.append(current.isoformat()[:10])
        current += timedelta(days=step_days)

    return dates


@pytest.fixture
def conftest_template() -> str:
    """
    Provide a ready-to-use conftest.py template for new test directories.
    """
    return get_conftest_template()
